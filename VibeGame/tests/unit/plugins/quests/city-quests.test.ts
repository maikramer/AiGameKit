import { describe, expect, it } from 'bun:test';

import city from '../../../../examples/simple-rpg/src/data/quests/city_quests.json';
import darkForest from '../../../../examples/simple-rpg/src/data/quests/dark_forest_quests.json';
import desert from '../../../../examples/simple-rpg/src/data/quests/desert_quests.json';
import swamp from '../../../../examples/simple-rpg/src/data/quests/swamp_quests.json';
import mountain from '../../../../examples/simple-rpg/src/data/quests/mountain_quests.json';

interface QuestObjectiveJson {
  type: string;
  target: string;
  count: number;
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

const CITY = city as QuestJson[];
const BIOME = [
  ...(darkForest as QuestJson[]),
  ...(desert as QuestJson[]),
  ...(swamp as QuestJson[]),
  ...(mountain as QuestJson[]),
];

describe('city quest catalogue', () => {
  it('ships four board bounties plus the blacksmith job', () => {
    expect(CITY.length).toBe(5);
    expect(CITY.filter((q) => q.npc === 'notice_board').length).toBe(4);
    expect(
      CITY.filter((q) => q.npc === 'npc_blacksmith').map((q) => q.id)
    ).toEqual(['city_stone']);
  });

  it('keeps ids unique against biome quests', () => {
    const ids = [...BIOME, ...CITY].map((q) => q.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('tags every city quest with biome city', () => {
    for (const q of CITY) expect(q.biome).toBe('city');
  });

  it('follows the dialogue writing rules', () => {
    for (const q of CITY) {
      expect(q.lines_intro.length).toBe(2);
      expect(q.lines_progress.length).toBe(1);
      expect(q.lines_complete.length).toBe(1);
      expect(q.lines_progress[0]).toContain('{remaining}');
    }
  });

  it('pays gold, xp and an item', () => {
    for (const q of CITY) {
      expect(q.rewards?.gold).toBeGreaterThan(0);
      expect(q.rewards?.xp).toBeGreaterThan(0);
      expect(q.rewards?.items?.length).toBeGreaterThan(0);
    }
  });

  it('uses kill/collect targets the game already reports', () => {
    const allowed = new Set([
      'wolf',
      'bandit',
      'goblin',
      'wood',
      'stone',
      'scorpion',
      'bogling',
      'slime',
      'shade',
    ]);
    for (const q of CITY) {
      expect(['kill', 'collect']).toContain(q.objective.type);
      expect(allowed.has(q.objective.target)).toBe(true);
      expect(q.objective.count).toBeGreaterThan(0);
    }
  });

  it('does not put a visit objective on the board (interact mode would stall it)', () => {
    for (const q of CITY) expect(q.objective.type).not.toBe('visit');
  });
});
