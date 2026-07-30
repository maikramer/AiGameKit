import type { ParserParams, Recipe } from '../../core';
import { logger } from '../../core/utils/logger';
import { internString } from '../hud/context';
import { registerHudWidget, type HudWidgetFactory } from '../hud/screen-layer';
import { readAttr } from '../hud/widgets/shared';
import { QuestGiver, DialogueData, QUEST_STATE_AVAILABLE } from './components';
import { getQuestIndex } from './registry';
import { dialogueBalloonFactory } from './hud/dialogue-balloon';
import { questTrackerFactory } from './hud/quest-tracker';

/** dialogue-ids already reported as unresolved (warn once, not per NPC). */
const warnedDialogueIds = new Set<string>();

/**
 * Quest giver NPC. `dialogue-id` selects which registered QuestDef this NPC
 * offers; `portrait-url`/`voice-sfx` populate the NPC-local DialogueData.
 */
export const dialogueNpcRecipe: Recipe = {
  name: 'DialogueNPC',
  merge: true,
  components: ['transform', 'quest-giver', 'dialogue-data'],
  parserAttributes: [
    'dialogue-id',
    'portrait-url',
    'voice-sfx',
    'marker-height',
  ],
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
  // Index 0 is a real quest, so falling back to it silently turns a typo — or a
  // scene that parses before the quests are registered — into "every NPC in the
  // game offers the first quest", which looks like a content bug rather than a
  // wiring one. Keep the fallback (an NPC without a quest is still a valid
  // greeter) but say so, once per id.
  if (dialogueId.length > 0 && idx < 0 && !warnedDialogueIds.has(dialogueId)) {
    warnedDialogueIds.add(dialogueId);
    logger.warn(
      `[quests] <DialogueNPC dialogue-id="${dialogueId}"> has no registered quest ` +
        `— falling back to quest index 0. Register quests before the scene is ` +
        `parsed (runtime.start()), and check the id matches a QuestDef.`
    );
  }
  QuestGiver.questId[entity] = idx < 0 ? 0 : idx;
  QuestGiver.state[entity] = QUEST_STATE_AVAILABLE;
  QuestGiver.acknowledged[entity] = 0;
  const markerHeight = Number(readAttr(element.attributes, 'marker-height'));
  QuestGiver.markerHeight[entity] = Number.isFinite(markerHeight)
    ? markerHeight
    : 0;

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

/** `<QuestTracker anchor="top-right" max-rows="4"/>` HUD corner. */
export const questTrackerRecipe: Recipe = {
  name: 'QuestTracker',
  components: [],
  parserAttributes: ['anchor', 'max-rows'],
  parserOwnsChildren: false,
};

export function questTrackerParser({ element, state }: ParserParams): void {
  registerHudWidget(state, questTrackerFactory(element.attributes, state));
}

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
