import type { State } from '../../core';
import { getDataRegistry } from '../rpg-core';
import { MAX_QUESTS, QuestState } from './components';

export type QuestObjectiveType = 'kill' | 'collect' | 'talk';

export interface QuestObjective {
  readonly type: QuestObjectiveType;
  readonly target: string;
  readonly count: number;
}

export interface QuestRewards {
  readonly gold?: number;
  readonly xp?: number;
  /** Each entry is `itemId` or `itemId:qty`. */
  readonly items?: readonly string[];
}

/** Declarative quest definition (spec §5). Lives in the DataRegistry. */
export interface QuestDef {
  readonly id: string;
  readonly npc: string;
  readonly biome?: string;
  readonly title: string;
  readonly portrait?: string;
  readonly voice?: string;
  readonly lines_intro: readonly string[];
  readonly lines_progress: readonly string[];
  readonly lines_complete: readonly string[];
  readonly objective: QuestObjective;
  readonly rewards?: QuestRewards;
}

const stateToQuestIndex = new WeakMap<State, Map<string, number>>();

function questIndexMap(state: State): Map<string, number> {
  let m = stateToQuestIndex.get(state);
  if (!m) {
    m = new Map();
    stateToQuestIndex.set(state, m);
  }
  return m;
}

/**
 * Register a quest definition and allocate its stable index (0..MAX_QUESTS-1).
 * Re-registering an existing id updates the definition but keeps its index.
 * Returns the allocated index; throws if MAX_QUESTS is exceeded.
 */
export function registerQuest(state: State, def: QuestDef): number {
  const map = questIndexMap(state);
  const existing = map.get(def.id);
  let index: number;
  if (existing !== undefined) {
    index = existing;
  } else {
    if (map.size >= MAX_QUESTS) {
      throw new Error(
        `[quests] cannot register "${def.id}": MAX_QUESTS (${MAX_QUESTS}) reached`
      );
    }
    index = map.size;
    map.set(def.id, index);
  }
  getDataRegistry(state).register('quest', def.id, def);
  return index;
}

/** Resolve the stable index for a quest id, or -1 if unregistered. */
export function getQuestIndex(state: State, id: string): number {
  const idx = questIndexMap(state).get(id);
  return idx === undefined ? -1 : idx;
}

export function getQuestDef(state: State, id: string): QuestDef | undefined {
  return getDataRegistry(state).get<QuestDef>('quest', id);
}

export function getQuestDefByIndex(
  state: State,
  index: number
): QuestDef | undefined {
  const map = questIndexMap(state);
  for (const [id, idx] of map) {
    if (idx === index) return getQuestDef(state, id);
  }
  return undefined;
}

export function getAllQuestDefs(state: State): readonly QuestDef[] {
  return getDataRegistry(state).all<QuestDef>('quest');
}

export interface QuestStateSnapshot {
  active: number[];
  progress: Record<number, number>;
  completed: number[];
}

/** Serialize the global QuestState into a JSON-safe snapshot (spec §10). */
export function serializeQuestState(state: State): QuestStateSnapshot {
  const active: number[] = [];
  const progress: Record<number, number> = {};
  const completed: number[] = [];
  const defs = getAllQuestDefs(state);
  for (const def of defs) {
    const idx = getQuestIndex(state, def.id);
    if (idx < 0) continue;
    if (QuestState.active[idx] === 1) active.push(idx);
    if (QuestState.progress[idx] > 0) progress[idx] = QuestState.progress[idx];
    if (QuestState.completed[idx] === 1) completed.push(idx);
  }
  return { active, progress, completed };
}

/**
 * Restore QuestState from a snapshot. Missing fields default to empty
 * (back-compat with saves authored before the quests plugin existed).
 */
export function applyQuestStateSnapshot(
  _state: State,
  data: Partial<QuestStateSnapshot> | null | undefined
): void {
  QuestState.active.fill(0);
  QuestState.progress.fill(0);
  QuestState.completed.fill(0);
  if (!data) return;
  for (const idx of data.active ?? []) {
    if (idx >= 0 && idx < MAX_QUESTS) QuestState.active[idx] = 1;
  }
  for (const [idxStr, count] of Object.entries(data.progress ?? {})) {
    const idx = Number(idxStr);
    if (Number.isInteger(idx) && idx >= 0 && idx < MAX_QUESTS) {
      QuestState.progress[idx] = Math.max(0, Math.floor(count));
    }
  }
  for (const idx of data.completed ?? []) {
    if (idx >= 0 && idx < MAX_QUESTS) QuestState.completed[idx] = 1;
  }
}
