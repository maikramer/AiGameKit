import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  FACTION_TAG_NAMES,
  FactionComponent,
  Health,
  ProjectileConfig,
  ProjectileData,
  bindCombatState,
  damageHealth,
  getDeathFlags,
  getFaction,
  healHealth,
  incrementProjectileAge,
  isAlive,
  isDead,
  isHostile,
  isProjectileExpired,
  setFaction,
  setMaxHealth,
  setProjectileOwner,
} from '../../../src/plugins/combat/components';
import { getDataRegistry } from '../../../src/plugins/rpg-core/registry';

describe('combat matrix: Health lifecycle', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    bindCombatState(state);
    eid = state.createEntity();
    state.addComponent(eid, Health);
    setMaxHealth(eid, 100);
  });

  for (const [label, dmg, expected] of [
    ['small hit', 10, 90],
    ['lethal', 100, 0],
    ['overkill capped at zero', 150, 0],
  ] as const) {
    it(`damageHealth ${label}`, () => {
      damageHealth(eid, dmg);
      expect(Health.current[eid]).toBe(expected);
    });
  }

  it('damage on dead entity is no-op', () => {
    Health.current[eid] = 0;
    damageHealth(eid, 50);
    expect(Health.current[eid]).toBe(0);
  });

  it('healHealth respects max', () => {
    damageHealth(eid, 40);
    healHealth(eid, 100);
    expect(Health.current[eid]).toBe(100);
  });

  it('isAlive/isDead reflect HP', () => {
    expect(isAlive(eid)).toBe(true);
    expect(isDead(eid)).toBe(false);
    damageHealth(eid, 100);
    expect(isAlive(eid)).toBe(false);
    expect(isDead(eid)).toBe(true);
  });

  it('heal clears death flag', () => {
    damageHealth(eid, 100);
    getDeathFlags(state)[eid] = 1;
    healHealth(eid, 10);
    expect(getDeathFlags(state)[eid]).toBe(0);
  });
});

describe('combat matrix: setMaxHealth', () => {
  it('sets current to max', () => {
    const state = new State();
    bindCombatState(state);
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    setMaxHealth(eid, 50);
    expect(Health.max[eid]).toBe(50);
    expect(Health.current[eid]).toBe(50);
  });
});

describe('combat matrix: ProjectileData', () => {
  let eid: number;

  beforeEach(() => {
    const state = new State();
    eid = state.createEntity();
    state.addComponent(eid, ProjectileData);
    ProjectileData.lifetime[eid] = 2;
    ProjectileData.age[eid] = 0;
  });

  for (const [dt, expired] of [
    [0.5, false],
    [1.9, false],
    [2.0, true],
    [3.0, true],
  ] as const) {
    it(`projectile age ${dt}s expired=${expired}`, () => {
      ProjectileData.age[eid] = 0;
      incrementProjectileAge(eid, dt);
      expect(isProjectileExpired(eid)).toBe(expired);
    });
  }

  it('setProjectileOwner stores owner eid', () => {
    setProjectileOwner(eid, 42);
    expect(ProjectileData.ownerEid[eid]).toBe(42);
  });
});

describe('combat matrix: ProjectileConfig defaults', () => {
  it('ProjectileConfig arrays exist', () => {
    expect(ProjectileConfig.speed).toBeDefined();
    expect(ProjectileConfig.damage).toBeDefined();
  });
});

describe('combat matrix: factions', () => {
  let state: State;
  let a: number;
  let b: number;

  beforeEach(() => {
    state = new State();
    a = state.createEntity();
    b = state.createEntity();
    state.addComponent(a, FactionComponent);
    state.addComponent(b, FactionComponent);
  });

  it('setFaction and getFaction roundtrip', () => {
    setFaction(state, a, 'player');
    expect(getFaction(state, a)).toBe('player');
  });

  it('registers new faction tags dynamically', () => {
    const before = FACTION_TAG_NAMES.length;
    setFaction(state, a, 'custom-faction-xyz');
    expect(FACTION_TAG_NAMES.length).toBeGreaterThanOrEqual(before);
    expect(getFaction(state, a)).toBe('custom-faction-xyz');
  });

  it('isHostile false without matrix data', () => {
    setFaction(state, a, 'a');
    setFaction(state, b, 'b');
    expect(isHostile(state, a, b)).toBe(false);
  });

  it('isHostile true when matrix lists pair', () => {
    setFaction(state, a, 'hero');
    setFaction(state, b, 'slime');
    getDataRegistry(state).register('faction-hostility', 'default', {
      pairs: [['hero', 'slime']],
    });
    expect(isHostile(state, a, b)).toBe(true);
    expect(isHostile(state, b, a)).toBe(true);
  });

  for (const pair of [
    ['player', 'enemy'],
    ['guard', 'bandit'],
  ]) {
    it(`hostility matrix accepts pair ${pair.join(' vs ')}`, () => {
      const s = new State();
      const x = s.createEntity();
      const y = s.createEntity();
      s.addComponent(x, FactionComponent);
      s.addComponent(y, FactionComponent);
      setFaction(s, x, pair[0]!);
      setFaction(s, y, pair[1]!);
      getDataRegistry(s).register('faction-hostility', 'default', {
        pairs: [[pair[0], pair[1]]],
      });
      expect(isHostile(s, x, y)).toBe(true);
    });
  }
});

describe('combat matrix: FACTION_TAG_NAMES bootstrap', () => {
  it('includes player faction id 0', () => {
    expect(FACTION_TAG_NAMES[0]).toBe('player');
  });
  it('tag array is non-empty', () => {
    expect(FACTION_TAG_NAMES.length).toBeGreaterThan(0);
  });
});
