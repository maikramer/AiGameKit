import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import {
  Health,
  bindCombatState,
  clearDamageModifiers,
  damageHealth,
  grantInvulnerability,
  registerDamageModifier,
} from '../../../../src/plugins/combat/components';
import {
  COMBAT_DAMAGED,
  COMBAT_KILLED,
  onEvent,
} from '../../../../src/plugins/rpg-core/events';

describe('damageHealth source + damage modifiers', () => {
  let state: State;
  let events: { type: string; payload: Record<string, unknown> }[];

  beforeEach(() => {
    clearDamageModifiers();
    state = new State();
    state.registerComponent('health', Health);
    bindCombatState(state);
    events = [];
    onEvent(state, COMBAT_DAMAGED, (p) => {
      events.push({ type: 'damaged', payload: p as Record<string, unknown> });
    });
    onEvent(state, COMBAT_KILLED, (p) => {
      events.push({ type: 'killed', payload: p as Record<string, unknown> });
    });
  });

  function makeEntity(hp: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = hp;
    Health.max[eid] = hp;
    Health.invulnTimer[eid] = 0;
    return eid;
  }

  it('passes the source through to the combat:damaged payload', () => {
    const target = makeEntity(100);
    damageHealth(target, 10, 42);
    expect(Health.current[target]).toBe(90);
    expect(events).toHaveLength(1);
    expect(events[0]!.payload.attacker).toBe(42);
    expect(events[0]!.payload.amount).toBe(10);
  });

  it('omits the attacker field for legacy callers (no source)', () => {
    const target = makeEntity(100);
    damageHealth(target, 10);
    expect(events[0]!.payload.attacker).toBeUndefined();
  });

  it('modifier reduces damage before HP is written', () => {
    const target = makeEntity(100);
    registerDamageModifier((_eid, amount) => amount * 0.5);
    damageHealth(target, 20);
    expect(Health.current[target]).toBe(90);
    expect(events[0]!.payload.amount).toBe(10);
  });

  it('a modifier returning <= 0 negates the blow (no HP change, no events)', () => {
    const target = makeEntity(100);
    registerDamageModifier((eid, amount, source) =>
      eid === target && source === 7 ? 0 : amount
    );
    damageHealth(target, 20, 7);
    expect(Health.current[target]).toBe(100);
    expect(events).toHaveLength(0);

    damageHealth(target, 20, 8);
    expect(Health.current[target]).toBe(80);
    expect(events).toHaveLength(1);
  });

  it('modifiers compose in registration order', () => {
    const target = makeEntity(100);
    const order: string[] = [];
    registerDamageModifier((_eid, amount) => {
      order.push('armor');
      return amount - 5;
    });
    registerDamageModifier((_eid, amount) => {
      order.push('doubler');
      return amount * 2;
    });
    damageHealth(target, 10);
    expect(order).toEqual(['armor', 'doubler']);
    expect(Health.current[target]).toBe(90); // (10 - 5) * 2
  });

  it('unregister stops the modifier from applying', () => {
    const target = makeEntity(100);
    const off = registerDamageModifier(() => 0);
    off();
    damageHealth(target, 20);
    expect(Health.current[target]).toBe(80);
    expect(events).toHaveLength(1);
  });

  it('i-frames still take precedence over modifiers', () => {
    const target = makeEntity(100);
    registerDamageModifier(() => 99);
    grantInvulnerability(target, 1);
    damageHealth(target, 20);
    expect(Health.current[target]).toBe(100);
    expect(events).toHaveLength(0);
  });

  it('negated killing blow does not emit combat:killed', () => {
    const target = makeEntity(100);
    registerDamageModifier((eid, amount) => (eid === target ? 0 : amount));
    damageHealth(target, 200);
    expect(Health.current[target]).toBe(100);
    expect(events.filter((e) => e.type === 'killed')).toHaveLength(0);
  });
});
