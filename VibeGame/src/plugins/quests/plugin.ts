import type { Plugin, State } from '../../core';
import { registerGlobalSaveSerializer } from '../save-load/serializer-registry';
import {
  dialogueBalloonParser,
  dialogueBalloonRecipe,
  dialogueNpcParser,
  dialogueNpcRecipe,
  questTrackerParser,
  questTrackerRecipe,
  questsTabRecipe,
} from './recipes';
// Importing the factories loads their modules, which self-register the
// 'dialogue-balloon' / 'quest-tracker' HUD widget factories as a side effect.
import { dialogueBalloonFactory } from './hud/dialogue-balloon';
import { questTrackerFactory } from './hud/quest-tracker';
import { DialogueData, QuestGiver } from './components';
import { QuestBeaconSystem } from './beacon';
import { QuestGiverFacingSystem } from './facing';
import { QuestMarkerSystem } from './markers';
import {
  QuestProgressSystem,
  QuestTriggerSystem,
  QuestVisitSystem,
} from './systems';
import {
  applyQuestStateSnapshot,
  serializeQuestState,
  type QuestStateSnapshot,
} from './registry';

// QuestsTab is interpreted by the TabbedModal child builder; no parse work.
function questsTabParser(): void {}

export const QuestsPlugin: Plugin = {
  systems: [
    QuestTriggerSystem,
    QuestProgressSystem,
    QuestVisitSystem,
    QuestGiverFacingSystem,
    QuestBeaconSystem,
    QuestMarkerSystem,
  ],
  recipes: [
    dialogueNpcRecipe,
    questsTabRecipe,
    dialogueBalloonRecipe,
    questTrackerRecipe,
  ],
  components: {
    'quest-giver': QuestGiver,
    'dialogue-data': DialogueData,
  },
  config: {
    defaults: {
      'quest-giver': {
        state: 0,
        questId: 0,
        acknowledged: 0,
        markerHeight: 0,
      },
      'dialogue-data': { linesIndex: 0, portraitId: 0, voiceId: 0 },
    },
    parsers: {
      DialogueNPC: dialogueNpcParser,
      QuestsTab: questsTabParser,
      DialogueBalloon: dialogueBalloonParser,
      QuestTracker: questTrackerParser,
    },
  },
  initialize(state: State): void {
    void dialogueBalloonFactory;
    void questTrackerFactory;
    registerGlobalSaveSerializer(state, 'quests', {
      serialize: (s) => serializeQuestState(s),
      deserialize: (s, data) =>
        applyQuestStateSnapshot(s, data as Partial<QuestStateSnapshot> | null),
    });
  },
};
