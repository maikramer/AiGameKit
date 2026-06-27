import { MAX_ENTITIES } from '../../core/ecs/constants';

/** Maximum number of distinct quests trackable at once (per spec §4.1). */
export const MAX_QUESTS = 64;

/** QuestGiver.state values. */
export const QUEST_STATE_AVAILABLE = 0;
export const QUEST_STATE_TAKEN = 1;
export const QUEST_STATE_COMPLETED = 2;
export const QUEST_STATE_FAILED = 3;

/**
 * Attached to an NPC entity. `questId` stores the quest index (allocated by
 * {@link registerQuest}); `state` is the per-NPC dialogue/state-machine slot.
 */
export const QuestGiver = {
  questId: new Uint32Array(MAX_ENTITIES),
  state: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Per-NPC dialogue metadata. `linesIndex`/`portraitId`/`voiceId` hold interned
 * string-pool indices (see `hud/context` `internString`). The dialogue payload
 * shown to the player is sourced from the {@link QuestDef} lines arrays; these
 * fields allow NPC-local overrides of portrait/voice without a registry lookup.
 */
export const DialogueData = {
  linesIndex: new Uint32Array(MAX_ENTITIES),
  portraitId: new Uint32Array(MAX_ENTITIES),
  voiceId: new Uint32Array(MAX_ENTITIES),
} as const;

/**
 * Global quest-progress singleton, indexed by quest index (0..MAX_QUESTS-1).
 * `active[i]===1` means the quest is currently accepted; `progress[i]` is the
 * current objective count; `completed[i]===1` means the quest was finished.
 */
export const QuestState = {
  active: new Uint8Array(MAX_QUESTS),
  progress: new Uint32Array(MAX_QUESTS),
  completed: new Uint8Array(MAX_QUESTS),
} as const;

/** Reset the global QuestState singleton (primarily for tests). */
export function resetQuestState(): void {
  QuestState.active.fill(0);
  QuestState.progress.fill(0);
  QuestState.completed.fill(0);
}
