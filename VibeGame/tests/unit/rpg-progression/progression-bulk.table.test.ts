import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  PROGRESSION_LEVEL_UP,
  PROGRESSION_XP_GAINED,
  ProgressionComponent,
  ProgressionPlugin,
  State,
  addXp,
  getProgressionConfig,
  getSkillRank,
  getXpToNextLevel,
  levelUp,
  onEvent,
  setProgressionConfig,
} from 'vibegame';
import {
  applyProgressionEntitySnapshot,
  getProgressionEntitySnapshot,
} from '../../../src/plugins/rpg-progression/components';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

describe('getXpToNextLevel default curve', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (let level = 1; level <= 20; level += 1) {
    it(`level ${level} needs 5+level xp`, () => {
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.level[eid] = level;
      expect(getXpToNextLevel(state, eid)).toBe(5 + level);
    });
  }

  it('returns 0 without component', () => {
    const eid = state.createEntity();
    expect(getXpToNextLevel(state, eid)).toBe(0);
  });
});

describe('addXp incremental amounts', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (let amount = 1; amount <= 30; amount += 1) {
    it(`addXp ${amount} on fresh entity`, () => {
      const eid = state.createFromRecipe('Progression');
      const levelBefore = ProgressionComponent.level[eid];
      addXp(state, eid, amount);
      if (amount < 6) {
        expect(ProgressionComponent.level[eid]).toBe(1);
        expect(ProgressionComponent.xp[eid]).toBe(amount);
      } else {
        expect(ProgressionComponent.level[eid]).toBeGreaterThanOrEqual(
          levelBefore + 1
        );
      }
    });
  }
});

describe('levelUp grants skill points from config', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (const points of [1, 2, 3, 5, 10]) {
    it(`skillPointsPerLevel=${points}`, () => {
      const eid = state.createFromRecipe('Progression');
      setProgressionConfig(state, eid, { skillPointsPerLevel: points });
      ProgressionComponent.xp[eid] = 100;
      levelUp(state, eid);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(points);
      expect(ProgressionComponent.level[eid]).toBe(2);
    });
  }
});

describe('progression snapshot roundtrip', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (let level = 2; level <= 12; level += 1) {
    it(`level ${level} snapshot`, () => {
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.level[eid] = level;
      ProgressionComponent.xp[eid] = level * 3;
      ProgressionComponent.unspentPoints[eid] = level;
      ProgressionComponent.spent[eid] = 1;
      setProgressionConfig(state, eid, {
        xpCurve: 'default',
        skillPointsPerLevel: 3,
      });
      const snap = getProgressionEntitySnapshot(state, eid);
      expect(snap).not.toBeNull();
      applyProgressionEntitySnapshot(state, eid, snap!);
      expect(ProgressionComponent.level[eid]).toBe(level);
      expect(getProgressionConfig(state, eid).skillPointsPerLevel).toBe(3);
    });
  }
});

describe('progression events queued until step', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (let xp of [6, 12, 18, 24]) {
    it(`xp=${xp} emits after step`, () => {
      const eid = state.createFromRecipe('Progression');
      const seen: string[] = [];
      onEvent(state, PROGRESSION_XP_GAINED, () => seen.push('xp'));
      onEvent(state, PROGRESSION_LEVEL_UP, () => seen.push('level'));
      addXp(state, eid, xp);
      expect(seen).toEqual([]);
      state.step();
      expect(seen.length).toBeGreaterThan(0);
    });
  }
});

describe('getSkillRank default zero', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  for (let i = 0; i < 20; i += 1) {
    it(`entity ${i} rank unset`, () => {
      const eid = state.createFromRecipe('Progression');
      expect(getSkillRank(state, eid, `skill-${i}`)).toBe(0);
    });
  }
});
