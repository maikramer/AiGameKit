import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Health } from '../../../src/plugins/combat/components';
import {
  clearCombatTarget,
  getCombatTarget,
  getCombatTargetLabel,
  setCombatTarget,
  tickCombatTarget,
} from '../../../src/plugins/combat/combat-target';

describe('combat-target soft-lock', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    clearCombatTarget();
  });

  afterEach(() => {
    clearCombatTarget();
  });

  it('sets and clears a target', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = 40;
    Health.max[eid] = 40;

    setCombatTarget(eid, { label: 'Slime', ttl: 3 });
    expect(getCombatTarget()).toBe(eid);
    expect(getCombatTargetLabel()).toBe('Slime');

    clearCombatTarget();
    expect(getCombatTarget()).toBe(-1);
  });

  it('expires when HP hits zero', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = 10;
    Health.max[eid] = 10;
    setCombatTarget(eid, { label: 'Goblin' });

    Health.current[eid] = 0;
    tickCombatTarget(state, 0.016);
    expect(getCombatTarget()).toBe(-1);
  });

  it('expires after TTL', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = 10;
    Health.max[eid] = 10;
    setCombatTarget(eid, { ttl: 0.5 });

    tickCombatTarget(state, 0.6);
    expect(getCombatTarget()).toBe(-1);
  });
});
