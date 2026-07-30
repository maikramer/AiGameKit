import { describe, expect, it } from 'bun:test';
import {
  DialogueData,
  QuestGiver,
} from '../../../../src/plugins/quests/components';
import {
  dialogueBalloonRecipe,
  dialogueNpcRecipe,
  questsTabRecipe,
} from '../../../../src/plugins/quests/recipes';
import {
  QuestProgressSystem,
  QuestTriggerSystem,
  QuestVisitSystem,
} from '../../../../src/plugins/quests/systems';
import { QuestBeaconSystem } from '../../../../src/plugins/quests/beacon';
import { QuestGiverFacingSystem } from '../../../../src/plugins/quests/facing';
import { QuestMarkerSystem } from '../../../../src/plugins/quests/markers';
import { QuestsPlugin } from '../../../../src/plugins/quests/plugin';

describe('QuestsPlugin shape', () => {
  it('registers trigger, progress, visit, facing, beacon and marker systems', () => {
    expect(QuestsPlugin.systems).toEqual([
      QuestTriggerSystem,
      QuestProgressSystem,
      QuestVisitSystem,
      QuestGiverFacingSystem,
      QuestBeaconSystem,
      QuestMarkerSystem,
    ]);
  });

  it('maps quest-giver and dialogue-data components', () => {
    expect(QuestsPlugin.components!['quest-giver']).toBe(QuestGiver);
    expect(QuestsPlugin.components!['dialogue-data']).toBe(DialogueData);
  });

  const recipes = [dialogueNpcRecipe, questsTabRecipe, dialogueBalloonRecipe];
  for (const recipe of recipes) {
    it(`recipe ${recipe.name} is listed on the plugin`, () => {
      expect(QuestsPlugin.recipes!.map((r) => r.name)).toContain(recipe.name);
    });
  }

  it('DialogueNPC recipe merges and wires parser attrs', () => {
    expect(dialogueNpcRecipe.merge).toBe(true);
    expect(dialogueNpcRecipe.parserAttributes).toContain('dialogue-id');
  });

  const defaults = QuestsPlugin.config!.defaults!;
  for (const [comp, values] of Object.entries(defaults)) {
    for (const [field, value] of Object.entries(
      values as Record<string, number>
    )) {
      it(`default ${comp}.${field} = ${value}`, () => {
        expect(
          (defaults[comp as keyof typeof defaults] as Record<string, number>)[
            field
          ]
        ).toBe(value);
      });
    }
  }

  it('registers parsers for DialogueNPC, QuestsTab, DialogueBalloon', () => {
    const parsers = QuestsPlugin.config!.parsers!;
    expect(typeof parsers.DialogueNPC).toBe('function');
    expect(typeof parsers.QuestsTab).toBe('function');
    expect(typeof parsers.DialogueBalloon).toBe('function');
  });

  for (let i = 0; i < 20; i++) {
    it(`initialize hook is a function (check ${i})`, () => {
      expect(typeof QuestsPlugin.initialize).toBe('function');
    });
  }
});
