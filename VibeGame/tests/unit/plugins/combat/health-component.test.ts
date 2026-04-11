import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  Health,
  damageHealth,
  healHealth,
  isAlive,
  isDead,
  setMaxHealth,
} from '../../../../src/plugins/combat/components';

describe('Health Component', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = 100;
    Health.max[eid] = 100;
  });

  it('should have correct default structure', () => {
    expect(Health.current).toBeDefined();
    expect(Health.max).toBeDefined();
  });

  it('damageHealth reduces current and clamps at 0', () => {
    damageHealth(eid, 30);
    expect(Health.current[eid]).toBe(70);

    damageHealth(eid, 100);
    expect(Health.current[eid]).toBe(0);
  });

  it('healHealth increases current and clamps at max', () => {
    damageHealth(eid, 50);
    expect(Health.current[eid]).toBe(50);

    healHealth(eid, 30);
    expect(Health.current[eid]).toBe(80);

    healHealth(eid, 50);
    expect(Health.current[eid]).toBe(100);
  });

  it('isAlive returns true when current > 0', () => {
    expect(isAlive(eid)).toBe(true);
    damageHealth(eid, 99);
    expect(isAlive(eid)).toBe(true);
  });

  it('isDead returns true when current <= 0', () => {
    damageHealth(eid, 100);
    expect(isDead(eid)).toBe(true);
  });

  it('damageHealth on already dead entity stays at 0', () => {
    damageHealth(eid, 100);
    expect(Health.current[eid]).toBe(0);
    damageHealth(eid, 50);
    expect(Health.current[eid]).toBe(0);
  });

  it('setMaxHealth sets both current and max to given value', () => {
    setMaxHealth(eid, 50);
    expect(Health.max[eid]).toBe(50);
    expect(Health.current[eid]).toBe(50);
  });
});
