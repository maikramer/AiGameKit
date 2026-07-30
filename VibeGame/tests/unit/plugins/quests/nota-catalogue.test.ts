import { describe, expect, it } from 'bun:test';

import darkForest from '../../../../examples/simple-rpg/src/data/quests/dark_forest_quests.json';
import desert from '../../../../examples/simple-rpg/src/data/quests/desert_quests.json';
import swamp from '../../../../examples/simple-rpg/src/data/quests/swamp_quests.json';
import mountain from '../../../../examples/simple-rpg/src/data/quests/mountain_quests.json';
import {
  BIOME_IDS,
  LANDMARK_LABEL,
  NOTA_LANDMARKS,
  NOTA_MARK_RADIUS,
  SURVEY_QUEST,
  biomeOfLandmark,
  landmarkLabel,
  type BiomeId,
} from '../../../../examples/simple-rpg/src/data/nota-landmarks';

/**
 * A Nota (GDD F1) duplicates, by necessity, what the survey quests already
 * say: the landmark names and the radius live both in the catalogue (which
 * drives the `[F] Medir e assinar` prompt) and in `objective` (which the quest
 * plugin credits). These tests are the contract — drift means a prompt that
 * appears outside the range the quest accepts, or a landmark the player can
 * annotate that no quest ever counts.
 */

interface QuestObjectiveJson {
  type: string;
  target: string;
  count: number;
  radius?: number;
}

interface QuestJson {
  id: string;
  npc: string;
  biome?: string;
  title: string;
  lines_intro: string[];
  lines_progress: string[];
  lines_complete: string[];
  objective: QuestObjectiveJson;
  rewards?: { gold?: number; xp?: number; items?: string[] };
}

const ALL_QUESTS = [
  ...(darkForest as QuestJson[]),
  ...(desert as QuestJson[]),
  ...(swamp as QuestJson[]),
  ...(mountain as QuestJson[]),
];

function questById(id: string): QuestJson {
  const q = ALL_QUESTS.find((entry) => entry.id === id);
  if (!q) throw new Error(`missing quest ${id}`);
  return q;
}

describe('Nota landmark catalogue', () => {
  it('covers the four biomes with three landmarks each', () => {
    expect(BIOME_IDS.length).toBe(4);
    for (const biome of BIOME_IDS) {
      expect(NOTA_LANDMARKS[biome].length).toBe(3);
    }
  });

  it('has no landmark listed under two biomes', () => {
    const all = BIOME_IDS.flatMap((b) => [...NOTA_LANDMARKS[b]]);
    expect(new Set(all).size).toBe(all.length);
  });

  it('resolves each landmark back to its biome, and nothing else', () => {
    for (const biome of BIOME_IDS) {
      for (const name of NOTA_LANDMARKS[biome]) {
        expect(biomeOfLandmark(name)).toBe(biome);
      }
    }
    expect(biomeOfLandmark('forest-watch-tome')).toBeNull();
    expect(biomeOfLandmark('')).toBeNull();
  });

  it('names every landmark for the toast', () => {
    for (const biome of BIOME_IDS) {
      for (const name of NOTA_LANDMARKS[biome]) {
        expect(LANDMARK_LABEL[name]).toBeTruthy();
        expect(landmarkLabel(name)).not.toBe(name);
      }
    }
    // Unknown names fall back to the raw id rather than throwing.
    expect(landmarkLabel('nope')).toBe('nope');
  });
});

describe('catalogue ⇔ survey quest contract', () => {
  it('maps every biome to a registered survey quest', () => {
    for (const biome of BIOME_IDS) {
      const quest = questById(SURVEY_QUEST[biome]);
      expect(quest.objective.type).toBe('visit');
    }
  });

  it('lists exactly the quest targets, in the same set', () => {
    for (const biome of BIOME_IDS) {
      const quest = questById(SURVEY_QUEST[biome]);
      const questTargets = quest.objective.target.split(/\s+/).filter(Boolean);
      expect([...questTargets].sort()).toEqual(
        [...NOTA_LANDMARKS[biome]].sort()
      );
    }
  });

  it('asks for as many landmarks as the catalogue holds', () => {
    for (const biome of BIOME_IDS) {
      expect(questById(SURVEY_QUEST[biome]).objective.count).toBe(
        NOTA_LANDMARKS[biome].length
      );
    }
  });

  it('uses the same annotation radius on both sides', () => {
    for (const biome of BIOME_IDS) {
      expect(questById(SURVEY_QUEST[biome]).objective.radius).toBe(
        NOTA_MARK_RADIUS[biome]
      );
    }
  });

  it('keeps the radius inside the 9–12 m band the GDD specifies', () => {
    for (const biome of BIOME_IDS) {
      const r = NOTA_MARK_RADIUS[biome as BiomeId];
      expect(r).toBeGreaterThanOrEqual(9);
      expect(r).toBeLessThanOrEqual(12);
    }
  });
});

describe('quest catalogue shape (GDD 04-conteudo)', () => {
  it('ships sixteen quests, four per biome', () => {
    expect(ALL_QUESTS.length).toBe(16);
    for (const file of [darkForest, desert, swamp, mountain]) {
      expect((file as QuestJson[]).length).toBe(4);
    }
  });

  it('gives every biome exactly one survey quest', () => {
    const visits = ALL_QUESTS.filter((q) => q.objective.type === 'visit');
    expect(visits.length).toBe(4);
    expect(visits.map((q) => q.id).sort()).toEqual(
      BIOME_IDS.map((b) => SURVEY_QUEST[b]).sort()
    );
  });

  it('has unique ids and one giver each', () => {
    const ids = ALL_QUESTS.map((q) => q.id);
    expect(new Set(ids).size).toBe(ids.length);
    const npcs = ALL_QUESTS.map((q) => q.npc);
    expect(new Set(npcs).size).toBe(npcs.length);
  });

  it('follows the dialogue writing rules', () => {
    for (const q of ALL_QUESTS) {
      expect(q.lines_intro.length).toBe(2);
      expect(q.lines_progress.length).toBe(1);
      expect(q.lines_complete.length).toBe(1);
      // The progress line is the only place a count is shown.
      expect(q.lines_progress[0]).toContain('{remaining}');
    }
  });

  it('pays every quest in gold, xp and an item', () => {
    for (const q of ALL_QUESTS) {
      expect(q.rewards?.gold).toBeGreaterThan(0);
      expect(q.rewards?.xp).toBeGreaterThan(0);
      expect(q.rewards?.items?.length).toBeGreaterThan(0);
    }
  });

  it('no longer stands in a placeholder enemy for the Bog Warden', () => {
    // GDD flagged this: swamp_bogwarden shared `bogling` with swamp_boglings,
    // so two different NPCs asked for the same five kills.
    const warden = questById('swamp_bogwarden');
    expect(warden.objective.target).toBe('boss_bogwarden');
    expect(questById('swamp_boglings').objective.target).toBe('bogling');
  });
});
