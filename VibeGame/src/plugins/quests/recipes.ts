import type { ParserParams, Recipe } from '../../core';
import { internString } from '../hud/context';
import { registerHudWidget, type HudWidgetFactory } from '../hud/screen-layer';
import { readAttr } from '../hud/widgets/shared';
import { QuestGiver, DialogueData, QUEST_STATE_AVAILABLE } from './components';
import { getQuestIndex } from './registry';
import { dialogueBalloonFactory } from './hud/dialogue-balloon';

/**
 * Quest giver NPC. `dialogue-id` selects which registered QuestDef this NPC
 * offers; `portrait-url`/`voice-sfx` populate the NPC-local DialogueData.
 */
export const dialogueNpcRecipe: Recipe = {
  name: 'DialogueNPC',
  merge: true,
  components: ['transform', 'quest-giver', 'dialogue-data'],
  parserAttributes: ['dialogue-id', 'portrait-url', 'voice-sfx'],
};

export function dialogueNpcParser({
  entity,
  element,
  state,
}: ParserParams): void {
  const dialogueId = readAttr(element.attributes, 'dialogue-id') ?? '';
  const portraitUrl = readAttr(element.attributes, 'portrait-url');
  const voiceSfx = readAttr(element.attributes, 'voice-sfx');

  const idx = dialogueId.length > 0 ? getQuestIndex(state, dialogueId) : -1;
  QuestGiver.questId[entity] = idx < 0 ? 0 : idx;
  QuestGiver.state[entity] = QUEST_STATE_AVAILABLE;

  DialogueData.portraitId[entity] = portraitUrl
    ? internString(state, portraitUrl)
    : 0;
  DialogueData.voiceId[entity] = voiceSfx ? internString(state, voiceSfx) : 0;
  DialogueData.linesIndex[entity] = 0;
}

/** Marker recipe for the QuestsTab inside a `<TabbedModal>`. */
export const questsTabRecipe: Recipe = {
  name: 'QuestsTab',
  components: [],
  parserOwnsChildren: false,
};

/** Marker recipe for the DialogueBalloon HUD overlay. */
export const dialogueBalloonRecipe: Recipe = {
  name: 'DialogueBalloon',
  components: [],
  parserAttributes: ['portrait-url', 'portrait'],
  parserOwnsChildren: false,
};

export function dialogueBalloonParser({ element, state }: ParserParams): void {
  const factory: HudWidgetFactory = dialogueBalloonFactory;
  const widget = factory(element.attributes, state);
  registerHudWidget(state, widget);
}
