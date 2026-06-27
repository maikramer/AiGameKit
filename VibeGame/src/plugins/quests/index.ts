export {
  DialogueData,
  MAX_QUESTS,
  QuestGiver,
  QuestState,
  QUEST_STATE_AVAILABLE,
  QUEST_STATE_COMPLETED,
  QUEST_STATE_FAILED,
  QUEST_STATE_TAKEN,
  resetQuestState,
} from './components';
export {
  applyQuestStateSnapshot,
  getAllQuestDefs,
  getQuestDef,
  getQuestDefByIndex,
  getQuestIndex,
  registerQuest,
  serializeQuestState,
  type QuestDef,
  type QuestObjective,
  type QuestObjectiveType,
  type QuestRewards,
  type QuestStateSnapshot,
} from './registry';
export {
  acceptQuest,
  endDialogue,
  getActiveDialogue,
  showDialogue,
  type ActiveDialogue,
  type DialoguePhase,
} from './dialogue';
export {
  QUEST_COMPLETED,
  QuestProgressSystem,
  QuestTriggerSystem,
  notifyEnemyKilled,
  notifyResourceHarvested,
} from './systems';
export {
  dialogueBalloonParser,
  dialogueBalloonRecipe,
  dialogueNpcParser,
  dialogueNpcRecipe,
  questsTabRecipe,
} from './recipes';
export { createQuestsTab } from './hud/quests-tab';
export type { QuestsTabConfig } from './hud/quests-tab';
export { dialogueBalloonFactory } from './hud/dialogue-balloon';
export { QuestsPlugin } from './plugin';
