import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  PROGRESSION_LEVEL_UP,
  PROGRESSION_SKILL_PURCHASED,
  PROGRESSION_XP_GAINED,
  ProgressionComponent,
  ProgressionPlugin,
  State,
  XMLParser,
  addXp,
  getDataRegistry,
  getProgressionConfig,
  getSkillRank,
  getStatModifiers,
  levelUp,
  onEvent,
  parseXMLToEntities,
  spendSkillPoint,
} from 'vibegame';
import type { SkillDef } from 'vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

function registerVitality(state: State): void {
  getDataRegistry(state).register<SkillDef>('skill', 'vitality', {
    id: 'vitality',
    name: 'Vitality',
    maxRank: 5,
    cost: 1,
    effect: {
      kind: 'stat-modifier',
      payload: { stat: 'maxHp', magnitude: 12, stackMode: 'stack' },
    },
  });
}

describe('Progression plugin (XP / level / skills / stat modifiers)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(ProgressionPlugin);
  });

  describe('recipe + component defaults', () => {
    it('createFromRecipe attaches progression component starting at level 1', () => {
      const eid = state.createFromRecipe('Progression');
      expect(state.hasComponent(eid, ProgressionComponent)).toBe(true);
      expect(ProgressionComponent.level[eid]).toBe(1);
      expect(ProgressionComponent.xp[eid]).toBe(0);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(0);
      expect(ProgressionComponent.spent[eid]).toBe(0);
    });
  });

  describe('addXp triggers levelUp above threshold', () => {
    it('levels up once and carries remainder xp + grants skill points', () => {
      const eid = state.createFromRecipe('Progression', {
        'xp-curve': 'default',
      });

      addXp(state, eid, 7);

      expect(ProgressionComponent.level[eid]).toBe(2);
      expect(ProgressionComponent.xp[eid]).toBe(1);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(3);
    });

    it('can trigger multiple level-ups from a single addXp', () => {
      const eid = state.createFromRecipe('Progression');
      addXp(state, eid, 100);

      expect(ProgressionComponent.level[eid]).toBeGreaterThan(2);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(
        3 * (ProgressionComponent.level[eid] - 1)
      );
    });

    it('emits PROGRESSION_XP_GAINED and PROGRESSION_LEVEL_UP via the bridge system', () => {
      const eid = state.createFromRecipe('Progression');
      const events: string[] = [];
      onEvent(state, PROGRESSION_XP_GAINED, () => events.push('xp'));
      onEvent(state, PROGRESSION_LEVEL_UP, (p) => {
        events.push(`level:${(p as { level: number }).level}`);
      });

      addXp(state, eid, 7);
      expect(events).toEqual([]);

      state.step();
      expect(events).toContain('xp');
      expect(events).toContain('level:2');
    });
  });

  describe('spendSkillPoint increments rank and applies modifier', () => {
    it('spends a point, increments rank, and deducts unspent points', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 5;

      const ok = spendSkillPoint(state, eid, 'vitality');

      expect(ok).toBe(true);
      expect(getSkillRank(state, eid, 'vitality')).toBe(1);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(4);
      expect(ProgressionComponent.spent[eid]).toBe(1);
    });

    it('getStatModifiers includes the vitality maxHp modifier at rank 1', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 5;
      spendSkillPoint(state, eid, 'vitality');

      const mods = getStatModifiers(state, eid);
      const maxHp = mods.find((m) => m.stat === 'maxHp');
      expect(maxHp).toBeDefined();
      expect(maxHp?.magnitude).toBe(12);
    });

    it('stacking a skill scales magnitude with rank', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 5;
      spendSkillPoint(state, eid, 'vitality');
      spendSkillPoint(state, eid, 'vitality');

      const mods = getStatModifiers(state, eid);
      const maxHp = mods.find((m) => m.stat === 'maxHp');
      expect(maxHp?.magnitude).toBe(24);
    });

    it('returns false when there are no unspent points', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');

      expect(spendSkillPoint(state, eid, 'vitality')).toBe(false);
      expect(getSkillRank(state, eid, 'vitality')).toBe(0);
    });

    it('returns false when rank is already at maxRank', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 99;
      for (let i = 0; i < 5; i++) spendSkillPoint(state, eid, 'vitality');

      expect(getSkillRank(state, eid, 'vitality')).toBe(5);
      expect(spendSkillPoint(state, eid, 'vitality')).toBe(false);
    });

    it('emits PROGRESSION_SKILL_PURCHASED via the bridge system', () => {
      registerVitality(state);
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 5;
      const seen: unknown[] = [];
      onEvent(state, PROGRESSION_SKILL_PURCHASED, (p) => seen.push(p));

      spendSkillPoint(state, eid, 'vitality');
      expect(seen.length).toBe(0);
      state.step();
      expect(seen.length).toBe(1);
      expect((seen[0] as { skillId: string }).skillId).toBe('vitality');
    });
  });

  describe('event-trigger skill effect registers a handler on the EventBus', () => {
    it('fires a custom event when the configured trigger fires', () => {
      getDataRegistry(state).register<SkillDef>('skill', 'unlock-heal', {
        id: 'unlock-heal',
        name: 'Unlock Heal',
        maxRank: 1,
        cost: 1,
        effect: {
          kind: 'event-trigger',
          payload: {
            event: 'progression:level-up',
            triggers: 'custom:heal-full',
          },
        },
      });

      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 3;

      const healCalls: unknown[] = [];
      onEvent(state, 'custom:heal-full', (p) => healCalls.push(p));

      expect(spendSkillPoint(state, eid, 'unlock-heal')).toBe(true);

      // Trigger a level-up; the level-up event is drained by the bridge system.
      addXp(state, eid, 7);
      state.step();

      expect(ProgressionComponent.level[eid]).toBe(2);
      expect(healCalls.length).toBe(1);
    });
  });

  describe('levelUp direct call', () => {
    it('advances one level and grants skill points', () => {
      const eid = state.createFromRecipe('Progression');
      levelUp(state, eid);
      expect(ProgressionComponent.level[eid]).toBe(2);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(3);
    });
  });

  describe('getStatModifiers aggregation', () => {
    it('aggregates modifiers across multiple invested skills', () => {
      getDataRegistry(state).register<SkillDef>('skill', 'vitality', {
        id: 'vitality',
        name: 'Vitality',
        maxRank: 5,
        cost: 1,
        effect: {
          kind: 'stat-modifier',
          payload: { stat: 'maxHp', magnitude: 12, stackMode: 'stack' },
        },
      });
      getDataRegistry(state).register<SkillDef>('skill', 'strength', {
        id: 'strength',
        name: 'Strength',
        maxRank: 5,
        cost: 1,
        effect: {
          kind: 'stat-modifier',
          payload: { stat: 'attack', magnitude: 4, stackMode: 'stack' },
        },
      });

      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 10;
      spendSkillPoint(state, eid, 'vitality');
      spendSkillPoint(state, eid, 'strength');

      const mods = getStatModifiers(state, eid);
      expect(mods.map((m) => m.stat).sort()).toEqual(['attack', 'maxHp']);
    });

    it('ignores unlock and event-trigger effects', () => {
      getDataRegistry(state).register<SkillDef>('skill', 'sprint-unlock', {
        id: 'sprint-unlock',
        name: 'Sprint',
        maxRank: 1,
        cost: 1,
        effect: { kind: 'unlock', payload: { feature: 'sprint' } },
      });
      const eid = state.createFromRecipe('Progression');
      ProgressionComponent.unspentPoints[eid] = 3;
      spendSkillPoint(state, eid, 'sprint-unlock');

      expect(getStatModifiers(state, eid)).toEqual([]);
    });

    it('returns an empty array for an entity with no invested skills', () => {
      const eid = state.createFromRecipe('Progression');
      expect(getStatModifiers(state, eid)).toEqual([]);
    });
  });

  describe('XP curve is data-driven', () => {
    it('uses a custom registered xp-curve set programmatically via setProgressionConfig', () => {
      getDataRegistry(state).register('xp-curve', 'steep', {
        id: 'steep',
        fn: (lvl: number) => lvl * 100,
      });
      const eid = state.createFromRecipe('Progression');
      getProgressionConfig(state, eid).xpCurve = 'steep';

      addXp(state, eid, 150);
      expect(ProgressionComponent.level[eid]).toBe(2);
      expect(ProgressionComponent.xp[eid]).toBe(50);
    });

    it('<Progression xp-curve="..."> recipe attribute wires the curve via the parser', () => {
      getDataRegistry(state).register('xp-curve', 'flat', {
        id: 'flat',
        fn: () => 50,
      });
      const xml = `<Scene><Progression xp-curve="flat"/></Scene>`;
      const parsed = XMLParser.parse(xml);
      const [result] = parseXMLToEntities(state, parsed.root);
      const eid = result.entity;

      expect(getProgressionConfig(state, eid).xpCurve).toBe('flat');
      addXp(state, eid, 50);
      expect(ProgressionComponent.level[eid]).toBe(2);
      expect(ProgressionComponent.xp[eid]).toBe(0);
    });

    it('<Progression skill-points-per-level="N"> recipe attribute overrides points granted', () => {
      const xml = `<Scene><Progression skill-points-per-level="5"/></Scene>`;
      const parsed = XMLParser.parse(xml);
      const [result] = parseXMLToEntities(state, parsed.root);
      const eid = result.entity;

      expect(getProgressionConfig(state, eid).skillPointsPerLevel).toBe(5);
      addXp(state, eid, 7);
      expect(ProgressionComponent.unspentPoints[eid]).toBe(5);
    });
  });
});
