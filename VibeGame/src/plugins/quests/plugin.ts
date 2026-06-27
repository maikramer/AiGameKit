import type { Plugin, State } from '../../core';
import { registerGlobalSaveSerializer } from '../save-load/serializer-registry';
import {
  dialogueBalloonParser,
  dialogueBalloonRecipe,
  dialogueNpcParser,
  dialogueNpcRecipe,
  questsTabRecipe,
} from './recipes';
// Importing the factory loads its module, which self-registers the
// 'dialogue-balloon' HUD widget factory as a side effect.
import { dialogueBalloonFactory } from './hud/dialogue-balloon';
import { DialogueData, QuestGiver } from './components';
import { QuestProgressSystem, QuestTriggerSystem } from './systems';
import {
  applyQuestStateSnapshot,
  serializeQuestState,
  type QuestStateSnapshot,
} from './registry';

// QuestsTab is interpreted by the TabbedModal child builder; no parse work.
function questsTabParser(): void {}

export const QuestsPlugin: Plugin = {
  systems: [QuestTriggerSystem, QuestProgressSystem],
  recipes: [dialogueNpcRecipe, questsTabRecipe, dialogueBalloonRecipe],
  components: {
    'quest-giver': QuestGiver,
    'dialogue-data': DialogueData,
  },
  config: {
    defaults: {
      'quest-giver': { state: 0, questId: 0 },
      'dialogue-data': { linesIndex: 0, portraitId: 0, voiceId: 0 },
    },
    parsers: {
      DialogueNPC: dialogueNpcParser,
      QuestsTab: questsTabParser,
      DialogueBalloon: dialogueBalloonParser,
    },
  },
  initialize(state: State): void {
    void dialogueBalloonFactory;
    registerGlobalSaveSerializer(state, 'quests', {
      serialize: (s) => serializeQuestState(s),
      deserialize: (s, data) =>
        applyQuestStateSnapshot(s, data as Partial<QuestStateSnapshot> | null),
    });
  },
};
