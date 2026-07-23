import { beforeEach, describe, expect, it } from 'bun:test';
import {
  RpgCoreEventsPlugin,
  State,
  addResource,
  getCapacity,
  getResource,
  registerResourceKind,
  spendResource,
  setCapacity,
  VaultComponent,
  RpgVaultPlugin,
} from 'vibegame';
import {
  applyVaultEntitySnapshot,
  DEFAULT_VAULT_CAPACITY,
  getResourceKindIndex,
  getVaultEntitySnapshot,
} from '../../../src/plugins/rpg-vault/components';

describe('rpg-vault table-driven', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RpgCoreEventsPlugin);
    state.registerPlugin(RpgVaultPlugin);
    eid = state.createEntity();
  });

  const kinds = [
    'gold',
    'silver',
    'wood',
    'stone',
    'iron',
    'herbs',
    'mana',
    'xp-shards',
    'gems',
    'tokens',
    'energy',
    'food',
    'water',
    'ore',
    'cloth',
    'leather',
    'crystal',
    'dust',
    'keys',
    'reputation',
  ];

  for (const kind of kinds) {
    it(`addResource 10 ${kind} readable via getResource`, () => {
      addResource(state, eid, kind, 10);
      expect(getResource(state, eid, kind)).toBe(10);
      expect(VaultComponent.active[eid]).toBe(1);
    });
  }

  for (const kind of kinds) {
    it(`spendResource 5 of ${kind} after add 10`, () => {
      addResource(state, eid, kind, 10);
      expect(spendResource(state, eid, kind, 5)).toBe(true);
      expect(getResource(state, eid, kind)).toBe(5);
    });
  }

  for (let amount of [1, 5, 10, 50, 100, 500, 1000, 2500, 5000, 9999]) {
    it(`registerResourceKind stable index for amount-${amount}`, () => {
      const k = `res-${amount}`;
      const a = registerResourceKind(state, k);
      const b = registerResourceKind(state, k);
      expect(a).toBe(b);
    });
  }

  for (let cap of [10, 50, 100, 500, 1000, 2500, 5000, 9999, 10000, 50000]) {
    it(`setCapacity ${cap} clamps addResource`, () => {
      setCapacity(state, eid, 'gold', cap);
      addResource(state, eid, 'gold', cap + 1000);
      expect(getResource(state, eid, 'gold')).toBe(cap);
      expect(getCapacity(state, eid, 'gold')).toBe(cap);
    });
  }

  for (let i = 0; i < 15; i++) {
    it(`getResourceKindIndex undefined before register — ${i}`, () => {
      expect(getResourceKindIndex(state, `unknown-${i}`)).toBeUndefined();
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`spendResource fails on empty kind fail-${i}`, () => {
      expect(spendResource(state, eid, `fail-${i}`, 1)).toBe(false);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`addResource zero or negative no-op z-${i}`, () => {
      addResource(state, eid, 'gold', 0);
      addResource(state, eid, 'gold', -5);
      expect(getResource(state, eid, 'gold')).toBe(0);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`getCapacity default for unregistered kind def-${i}`, () => {
      expect(getCapacity(state, eid, `def-${i}`)).toBe(DEFAULT_VAULT_CAPACITY);
    });
  }

  for (let i = 0; i < 8; i++) {
    it(`snapshot roundtrip snap-${i}`, () => {
      addResource(state, eid, 'gold', 42 + i);
      setCapacity(state, eid, 'gold', 100);
      const snap = getVaultEntitySnapshot(state, eid);
      expect(snap).not.toBeNull();
      const e2 = state.createEntity();
      applyVaultEntitySnapshot(state, e2, snap!);
      expect(getResource(state, e2, 'gold')).toBe(42 + i);
    });
  }

  for (let i = 0; i < 5; i++) {
    it(`getVaultEntitySnapshot null when empty empty-${i}`, () => {
      expect(getVaultEntitySnapshot(state, eid)).toBeNull();
    });
  }

  for (let i = 1; i <= 10; i++) {
    it(`independent entities entity split ${i}`, () => {
      const e2 = state.createEntity();
      addResource(state, eid, 'gold', i);
      addResource(state, e2, 'gold', i * 10);
      expect(getResource(state, eid, 'gold')).toBe(i);
      expect(getResource(state, e2, 'gold')).toBe(i * 10);
    });
  }
});
