import { splitTokens } from '../../core';
import { defineSystem, defineQuery } from '../../core';
import type { State, System } from '../../core';
import { isKeyDown } from '../input';
import { PlayerController } from '../player';
import { Transform } from '../transforms';
import { addItem } from '../rpg-inventory';
import { addXp } from '../rpg-progression';
import { GOLD_KIND } from '../rpg-economy';
import { addResource } from '../rpg-vault';
import { emitEvent } from '../rpg-core';
import { getActiveDialogue, showDialogue } from './dialogue';
import {
  getAllQuestDefs,
  getQuestDefByIndex,
  getQuestIndex,
  type QuestDef,
} from './registry';
import {
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_TAKEN,
} from './components';

export const QUEST_COMPLETED = 'quest:completed';

// Matches the InteractionPrompt widget's default range: a prompt that appears
// half a metre before F actually works reads as an unresponsive NPC.
const DIALOGUE_RANGE = 4.5;
const DIALOGUE_RANGE_SQ = DIALOGUE_RANGE * DIALOGUE_RANGE;
const INTERACT_KEY = 'KeyF';

const giverQuery = defineQuery([QuestGiver]);
const playerQuery = defineQuery([PlayerController]);

const stateToFHeld = new WeakMap<State, boolean>();

function consumeFPress(state: State): boolean {
  const held = isKeyDown(INTERACT_KEY);
  const prev = stateToFHeld.get(state) ?? false;
  stateToFHeld.set(state, held);
  return held && !prev;
}

function resolvePlayer(state: State): number {
  const players = playerQuery(state.world);
  return players[0] ?? 0;
}

/**
 * Opens a dialogue with the nearest QuestGiver within range when the player
 * presses F. The phase (intro/progress/complete) is derived from the giver's
 * current state. Runs in `late` so it follows player movement.
 */
export const QuestTriggerSystem: System = defineSystem({
  name: 'QuestTriggerSystem',
  group: 'late',
  update(state: State): void {
    const fPressed = consumeFPress(state);
    if (getActiveDialogue(state) !== null || !fPressed) return;

    const playerEid = resolvePlayer(state);
    if (playerEid === 0) return;

    const px = Transform.posX[playerEid];
    const pz = Transform.posZ[playerEid];

    let nearestEid = 0;
    let nearestDist = Infinity;
    for (const eid of giverQuery(state.world)) {
      const dx = Transform.posX[eid] - px;
      const dz = Transform.posZ[eid] - pz;
      const d = dx * dx + dz * dz;
      if (d <= DIALOGUE_RANGE_SQ && d < nearestDist) {
        nearestDist = d;
        nearestEid = eid;
      }
    }
    if (nearestEid === 0) return;

    const def = getQuestDefByIndex(state, QuestGiver.questId[nearestEid]);
    if (!def) return;

    const giverState = QuestGiver.state[nearestEid];
    const phase =
      giverState === QUEST_STATE_AVAILABLE
        ? 'intro'
        : giverState === QUEST_STATE_TAKEN
          ? 'progress'
          : 'complete';

    showDialogue(state, { speakerEid: nearestEid, def, phase });
  },
});

interface PendingKill {
  readonly target: string;
  /** Objective kind the report may advance — kill reports never advance
   * `collect` objectives and vice-versa, even on name collisions. */
  readonly kind: 'kill' | 'collect';
}

const stateToKillQueue = new WeakMap<State, PendingKill[]>();

function killQueue(state: State): PendingKill[] {
  let q = stateToKillQueue.get(state);
  if (!q) {
    q = [];
    stateToKillQueue.set(state, q);
  }
  return q;
}

/**
 * Report an enemy kill so active `kill` objectives can advance. Called by game
 * scripts (e.g. enemy death handlers) — engine-side replacement for a missing
 * enemy-registry event API. Matches are processed next QuestProgressSystem tick.
 */
export function notifyEnemyKilled(state: State, target: string): void {
  killQueue(state).push({ target, kind: 'kill' });
}

/** Report a harvested resource so active `collect` objectives can advance. */
export function notifyResourceHarvested(state: State, kind: string): void {
  killQueue(state).push({ target: kind, kind: 'collect' });
}

function markGiverCompleted(state: State, questId: string): void {
  const idx = getQuestIndex(state, questId);
  if (idx < 0) return;
  for (const eid of giverQuery(state.world)) {
    if (QuestGiver.questId[eid] === idx) {
      QuestGiver.state[eid] = QUEST_STATE_COMPLETED;
    }
  }
}

function applyQuestRewards(state: State, def: QuestDef): void {
  const rewards = def.rewards;
  if (!rewards) return;
  const player = resolvePlayer(state);
  if (player === 0) return;
  if (rewards.gold && state.getComponent('vault')) {
    addResource(state, player, GOLD_KIND, rewards.gold);
  }
  if (rewards.xp && state.getComponent('progression')) {
    addXp(state, player, rewards.xp);
  }
  if (rewards.items && state.getComponent('inventory')) {
    for (const entry of rewards.items) {
      const sep = entry.indexOf(':');
      const itemId = sep >= 0 ? entry.slice(0, sep) : entry;
      const qty =
        sep >= 0 ? Math.max(1, parseInt(entry.slice(sep + 1), 10) || 1) : 1;
      addItem(state, player, itemId, qty);
    }
  }
}

/**
 * Drains pending kill/collect reports, advancing matching active quests and
 * completing them (emitting `quest:completed` + applying rewards) when the
 * objective count is reached.
 */
export const QuestProgressSystem: System = defineSystem({
  name: 'QuestProgressSystem',
  group: 'simulation',
  update(state: State): void {
    const queue = stateToKillQueue.get(state);
    if (!queue || queue.length === 0) return;
    const defs = getAllQuestDefs(state);
    const batch = queue.splice(0, queue.length);
    for (const item of batch) {
      for (const def of defs) {
        if (def.objective.type !== 'kill' && def.objective.type !== 'collect') {
          continue;
        }
        // Kind gate: a kill report must not advance a `collect` objective that
        // happens to share the target name (and vice-versa).
        if (def.objective.type !== item.kind) continue;
        if (def.objective.target !== item.target) continue;
        const idx = getQuestIndex(state, def.id);
        if (idx < 0) continue;
        if (QuestState.active[idx] !== 1 || QuestState.completed[idx] === 1) {
          continue;
        }
        const goal = Math.max(1, def.objective.count);
        const next = Math.min(goal, QuestState.progress[idx] + 1);
        QuestState.progress[idx] = next;
        if (next >= goal) {
          QuestState.completed[idx] = 1;
          QuestState.active[idx] = 0;
          markGiverCompleted(state, def.id);
          emitEvent(state, QUEST_COMPLETED, { questId: def.id, def });
          applyQuestRewards(state, def);
        }
      }
    }
  },
});

const DEFAULT_VISIT_RADIUS = 8;
/** Landmark names already reached, per quest id — a `visit` objective counts
 * each target once, no matter how often the player walks back through it. */
const stateToVisited = new WeakMap<State, Map<string, Set<string>>>();

/** Landmarks already reached for a quest — read by the map/beacon layer so a
 * visited stop stops being advertised as somewhere still to go. */
export function getVisitedTargets(
  state: State,
  questId: string
): ReadonlySet<string> {
  return stateToVisited.get(state)?.get(questId) ?? EMPTY_VISITED;
}

const EMPTY_VISITED: ReadonlySet<string> = new Set<string>();

/**
 * Restore which landmarks a quest already counted. Needed on load: the
 * progress *number* round-trips with QuestState, but without the names a
 * reloaded save would let the player re-credit a stop they already made.
 */
export function setVisitedTargets(
  state: State,
  questId: string,
  names: Iterable<string>
): void {
  const seen = visitedSet(state, questId);
  seen.clear();
  for (const name of names) seen.add(name);
}

function visitedSet(state: State, questId: string): Set<string> {
  let byQuest = stateToVisited.get(state);
  if (!byQuest) {
    byQuest = new Map();
    stateToVisited.set(state, byQuest);
  }
  let seen = byQuest.get(questId);
  if (!seen) {
    seen = new Set();
    byQuest.set(questId, seen);
  }
  return seen;
}

/**
 * How a `visit` objective decides that a landmark was reached.
 *
 * - `proximity` (default): walking inside `radius` counts it. Cheapest, and
 *   right for games where landmarks are just places on a route.
 * - `interact`: only an explicit {@link notifyLandmarkVisited} counts, so the
 *   game can put an action behind it (a survey, a prayer, planting a flag).
 *   Auto-counting would make that action decorative — the objective would
 *   already be ticked by the time the player pressed the key.
 */
export type QuestVisitMode = 'proximity' | 'interact';

const stateToVisitMode = new WeakMap<State, QuestVisitMode>();

export function setQuestVisitMode(state: State, mode: QuestVisitMode): void {
  stateToVisitMode.set(state, mode);
}

export function getQuestVisitMode(state: State): QuestVisitMode {
  return stateToVisitMode.get(state) ?? 'proximity';
}

const stateToVisitQueue = new WeakMap<State, string[]>();

/**
 * Report that the player registered a named landmark. Drives `visit`
 * objectives in `interact` mode, and is harmless in `proximity` mode (the
 * queue is drained the same way).
 */
export function notifyLandmarkVisited(state: State, name: string): void {
  let q = stateToVisitQueue.get(state);
  if (!q) {
    q = [];
    stateToVisitQueue.set(state, q);
  }
  q.push(name);
}

/** Count `name` towards every active `visit` quest that lists it. */
function creditVisit(state: State, name: string): void {
  for (const def of getAllQuestDefs(state)) {
    if (def.objective.type !== 'visit') continue;
    const idx = getQuestIndex(state, def.id);
    if (idx < 0) continue;
    if (QuestState.active[idx] !== 1 || QuestState.completed[idx] === 1) {
      continue;
    }
    if (!splitTokens(def.objective.target).includes(name)) continue;

    const seen = visitedSet(state, def.id);
    if (seen.has(name)) continue;
    seen.add(name);

    const goal = Math.max(1, def.objective.count);
    const next = Math.min(goal, QuestState.progress[idx] + 1);
    QuestState.progress[idx] = next;
    if (next >= goal) {
      QuestState.completed[idx] = 1;
      QuestState.active[idx] = 0;
      markGiverCompleted(state, def.id);
      emitEvent(state, QUEST_COMPLETED, { questId: def.id, def });
      applyQuestRewards(state, def);
    }
  }
}

/**
 * Advances `visit` objectives: the player reaching a named landmark.
 *
 * `kill`/`collect` are push-based (game scripts report events), but "go and
 * see this place" has nothing to report it — without this, a quest pointing at
 * a point of interest could never complete, so map landmarks could only ever
 * be scenery. Targets are resolved by entity name, so the quest data refers to
 * the same `name=` the scene XML already declares.
 *
 * In `interact` mode the proximity sweep is skipped entirely and only reported
 * visits count (see {@link setQuestVisitMode}).
 */
export const QuestVisitSystem: System = defineSystem({
  name: 'QuestVisitSystem',
  group: 'simulation',
  update(state: State): void {
    const queue = stateToVisitQueue.get(state);
    if (queue && queue.length > 0) {
      for (const name of queue.splice(0, queue.length)) {
        creditVisit(state, name);
      }
    }

    if (getQuestVisitMode(state) === 'interact') return;

    const player = resolvePlayer(state);
    if (player === 0) return;
    const px = Transform.posX[player];
    const pz = Transform.posZ[player];

    for (const def of getAllQuestDefs(state)) {
      if (def.objective.type !== 'visit') continue;
      const idx = getQuestIndex(state, def.id);
      if (idx < 0) continue;
      if (QuestState.active[idx] !== 1 || QuestState.completed[idx] === 1) {
        continue;
      }

      const radius = def.objective.radius ?? DEFAULT_VISIT_RADIUS;
      const radiusSq = radius * radius;
      const seen = visitedSet(state, def.id);

      for (const name of splitTokens(def.objective.target)) {
        if (!name || seen.has(name)) continue;
        const target = state.getEntityByName(name);
        if (target === null) continue;
        const dx = Transform.posX[target] - px;
        const dz = Transform.posZ[target] - pz;
        if (dx * dx + dz * dz > radiusSq) continue;

        creditVisit(state, name);
        if (QuestState.completed[idx] === 1) break;
      }
    }
  },
});

export { QUEST_STATE_AVAILABLE, QUEST_STATE_TAKEN, QUEST_STATE_COMPLETED };
