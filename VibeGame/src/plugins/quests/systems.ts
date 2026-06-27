import { defineQuery } from '../../core';
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

const DIALOGUE_RANGE = 4;
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
export const QuestTriggerSystem: System = {
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
};

interface PendingKill {
  readonly target: string;
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
  killQueue(state).push({ target });
}

/** Report a harvested resource so active `collect` objectives can advance. */
export function notifyResourceHarvested(state: State, kind: string): void {
  killQueue(state).push({ target: kind });
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
export const QuestProgressSystem: System = {
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
};

export { QUEST_STATE_AVAILABLE, QUEST_STATE_TAKEN, QUEST_STATE_COMPLETED };
