import { defineQuery, defineSystem, splitTokens } from '../../core';
import type { State, System } from '../../core';
import {
  type Waypoint,
  removeWaypoint,
  setTrackedWaypointId,
  setWaypoint,
  setWaypointAutoSelect,
  getWaypoints,
} from '../hud/waypoints';
import {
  registerInteractionTarget,
  unregisterInteractionTarget,
} from '../hud/widgets/interaction-prompt';
import { Transform } from '../transforms';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
} from './components';
import { resolveQuestMarkerKind } from './markers';
import {
  getAllQuestDefs,
  getQuestDefByIndex,
  getQuestIndex,
  type QuestDef,
} from './registry';
import { getVisitedTargets, getLastSeenTarget } from './systems';

/**
 * Turns quest state into navigation: head-badge state becomes map/compass
 * markers, and every quest giver becomes an interaction target so the `[F]`
 * prompt appears.
 *
 * Both halves close the same gap — a quest giver the engine knows about but
 * that the HUD never mentions is, from the player's side, scenery.
 */

/** Every waypoint this system owns is namespaced, so pruning can't touch
 * markers registered by game code. */
export const QUEST_WAYPOINT_PREFIX = 'quest:';

const GIVER_WAYPOINT = `${QUEST_WAYPOINT_PREFIX}giver:`;
const OBJECTIVE_WAYPOINT = `${QUEST_WAYPOINT_PREFIX}objective:`;

const giverQuery = defineQuery([QuestGiver, Transform]);

/** i18n key for the `[F]` prompt on a giver in the given state. */
export function questPromptKey(giverState: number): string {
  if (giverState === QUEST_STATE_AVAILABLE) return 'quests.prompt.talk';
  if (giverState === QUEST_STATE_COMPLETED) return 'quests.prompt.turnin';
  return 'quests.prompt.progress';
}

const stateToTrackedQuest = new WeakMap<State, string | null>();

/**
 * Pin the HUD arrow to one quest (by quest id), or `null` to let the beacon
 * choose (see {@link resolveTrackedQuestId}). The waypoint the arrow ends up
 * on is resolved each tick, so it follows the quest through accept →
 * objective → hand-in without the caller re-pinning.
 */
export function setTrackedQuest(state: State, questId: string | null): void {
  stateToTrackedQuest.set(state, questId);
  if (questId === null) {
    setTrackedWaypointId(state, null);
    setWaypointAutoSelect(state, true);
  }
}

export function getTrackedQuest(state: State): string | null {
  return stateToTrackedQuest.get(state) ?? null;
}

/**
 * The quest navigation actually follows: the player's explicit pin when set,
 * otherwise the first active quest. With a quest running, "where do I go"
 * means that quest — never the next one to pick up.
 */
export function resolveTrackedQuestId(state: State): string | null {
  return (
    stateToTrackedQuest.get(state) ??
    getAllActiveQuestDefs(state)[0]?.def.id ??
    null
  );
}

function objectiveLabel(def: QuestDef): string {
  return def.title;
}

/**
 * Waypoints for an active quest's objective. Only `visit` objectives have
 * fixed world positions; `kill`/`collect` targets roam or are scattered, so
 * pointing an arrow at one particular instance would be misleading.
 */
function publishObjectiveWaypoints(
  state: State,
  def: QuestDef,
  questIndex: number,
  live: Set<string>
): void {
  if (def.objective.type !== 'visit') return;
  const visited = getVisitedTargets(state, def.id);
  for (const name of splitTokens(def.objective.target)) {
    if (!name || visited.has(name)) continue;
    const eid = state.getEntityByName(name);
    if (eid === null) continue;
    const id = `${OBJECTIVE_WAYPOINT}${def.id}:${name}`;
    setWaypoint(state, {
      id,
      x: Transform.posX[eid],
      y: Transform.posY[eid],
      z: Transform.posZ[eid],
      kind: 'objective',
      label: objectiveLabel(def),
      eid,
      questIndex,
    });
    live.add(id);
  }
}

function publishGiverWaypoint(
  state: State,
  eid: number,
  def: QuestDef | undefined,
  live: Set<string>
): void {
  const questIndex = QuestGiver.questId[eid];
  const goal = Math.max(1, def?.objective.count ?? 1);
  const kind = resolveQuestMarkerKind(
    QuestGiver.state[eid],
    QuestState.progress[questIndex] ?? 0,
    goal,
    QuestGiver.acknowledged[eid] === 1
  );
  // 'progress' givers are deliberately silent on the map: while the objective
  // is out in the world, sending the player back to the NPC is wrong.
  if (kind !== 'available' && kind !== 'turnin') return;

  const id = `${GIVER_WAYPOINT}${eid}`;
  setWaypoint(state, {
    id,
    x: Transform.posX[eid],
    y: Transform.posY[eid],
    z: Transform.posZ[eid],
    kind: kind === 'available' ? 'quest-available' : 'quest-turnin',
    label: def?.title ?? state.getEntityName(eid) ?? '',
    eid,
    questIndex,
  });
  live.add(id);
}

/**
 * Waypoint for an active `kill`/`collect` quest: the spot where that target
 * was last reported killed or harvested. The targets themselves roam or are
 * scattered — the last event site is the only honest world position, and
 * before the first report there is nothing to point at.
 */
function publishFieldWaypoint(
  state: State,
  def: QuestDef,
  questIndex: number,
  live: Set<string>
): void {
  if (def.objective.type !== 'kill' && def.objective.type !== 'collect') return;
  const at = getLastSeenTarget(state, def.objective.type, def.objective.target);
  if (!at) return;
  const id = `${OBJECTIVE_WAYPOINT}${def.id}:last-seen`;
  setWaypoint(state, {
    id,
    x: at.x,
    y: at.y,
    z: at.z,
    kind: 'objective',
    label: def.title,
    questIndex,
  });
  live.add(id);
}

function resolveTrackedWaypointId(
  state: State,
  trackedQuest: string | null
): string | null {
  if (!trackedQuest) return null;
  const idx = getQuestIndex(state, trackedQuest);
  if (idx < 0) return null;
  let fallback: Waypoint | null = null;
  for (const wp of getWaypoints(state).values()) {
    if (wp.questIndex !== idx) continue;
    // An objective marker beats the giver marker: while a quest is running,
    // "where do I go" means the objective, not the person who gave it.
    if (wp.kind === 'objective') return wp.id;
    fallback = wp;
  }
  return fallback ? fallback.id : null;
}

/**
 * Rebuilds quest-owned waypoints and interaction targets each tick. Runs in
 * `late`, before `HudScreenUpdateSystem` consumes them in the same group.
 */
export const QuestBeaconSystem: System = defineSystem({
  name: 'QuestBeaconSystem',
  group: 'late',
  update(state: State): void {
    const live = new Set<string>();

    for (const eid of giverQuery(state.world)) {
      const def = getQuestDefByIndex(state, QuestGiver.questId[eid]);
      registerInteractionTarget(state, eid, {
        i18nKey: questPromptKey(QuestGiver.state[eid]),
        kind: 'quest',
        key: 'F',
      });
      publishGiverWaypoint(state, eid, def, live);
    }

    for (const def of getAllActiveQuestDefs(state)) {
      publishObjectiveWaypoints(state, def.def, def.index, live);
      publishFieldWaypoint(state, def.def, def.index, live);
    }

    for (const id of [...getWaypoints(state).keys()]) {
      if (!id.startsWith(QUEST_WAYPOINT_PREFIX)) continue;
      if (live.has(id)) continue;
      removeWaypoint(state, id);
    }

    const tracked = resolveTrackedQuestId(state);
    // While a quest is being followed, auto-selection is off: an active
    // kill/collect quest can legitimately have no world marker yet, and the
    // arrow going quiet must not let it slide onto a "new quest here" badge.
    setWaypointAutoSelect(state, tracked === null);
    setTrackedWaypointId(state, resolveTrackedWaypointId(state, tracked));
  },

  dispose(state: State): void {
    for (const eid of giverQuery(state.world)) {
      unregisterInteractionTarget(state, eid);
    }
    stateToTrackedQuest.delete(state);
  },
});

interface ActiveQuestEntry {
  readonly def: QuestDef;
  readonly index: number;
}

/**
 * Active (accepted, unfinished) quests with their stable indices. Driven by
 * the global QuestState rather than by the giver entities, so a quest whose
 * NPC is in an unloaded part of the world still guides the player.
 */
export function getAllActiveQuestDefs(state: State): ActiveQuestEntry[] {
  const out: ActiveQuestEntry[] = [];
  for (const def of getAllQuestDefs(state)) {
    const index = getQuestIndex(state, def.id);
    if (index < 0) continue;
    if (QuestState.active[index] !== 1) continue;
    if (QuestState.completed[index] === 1) continue;
    out.push({ def, index });
  }
  return out;
}
