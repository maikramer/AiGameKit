import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  CombatPlugin,
  COMBAT_DEATH,
  damageHealth,
  Health,
  onEvent,
  RpgCoreEventsPlugin,
  State,
  STATUS_APPLIED,
  STATUS_CANCELLED,
  STATUS_EXPIRED,
  StatusEffectComponent,
  StatusEffectsPlugin,
  applyStatus,
  cancelStatus,
  getActiveStatuses,
  getStatusModifiers,
  getDataRegistry,
} from 'vibegame';
import type { StatusEffectDef } from 'vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

const STATUS_KIND = 'status';

function def(
  id: string,
  overrides: Partial<StatusEffectDef> = {}
): StatusEffectDef {
  const base: StatusEffectDef = {
    id,
    name: id,
    duration: 5,
    modifiers: [],
  };
  return { ...base, ...overrides };
}

function newStateWithPlugins(): State {
  const state = new State();
  state.registerPlugin(RpgCoreEventsPlugin);
  state.registerPlugin(CombatPlugin);
  state.registerPlugin(StatusEffectsPlugin);
  return state;
}

function registerDefs(state: State): void {
  const registry = getDataRegistry(state);
  registry.register<StatusEffectDef>(
    STATUS_KIND,
    'speed-buff',
    def('speed-buff', {
      name: 'Speed Buff',
      duration: 10,
      modifiers: [{ stat: 'speed', magnitude: 1.3, stackMode: 'replace' }],
    })
  );
  registry.register<StatusEffectDef>(
    STATUS_KIND,
    'poison',
    def('poison', {
      name: 'Poison',
      duration: 10,
      tickInterval: 1,
      tickEffect: {
        kind: 'event-trigger',
        payload: { triggers: 'status:damage', amount: 2 },
      },
    })
  );
  registry.register<StatusEffectDef>(
    STATUS_KIND,
    'heal-over-time',
    def('heal-over-time', {
      name: 'Heal Over Time',
      duration: 6,
      tickInterval: 2,
      tickEffect: {
        kind: 'event-trigger',
        payload: { triggers: 'status:heal', amount: 5 },
      },
    })
  );
  registry.register<StatusEffectDef>(
    STATUS_KIND,
    'resolve-test',
    def('resolve-test', {
      name: 'Resolve',
      duration: 5,
      modifiers: [{ stat: 'speed', magnitude: 2, stackMode: 'stack' }],
    })
  );
}

describe('StatusEffectsPlugin — apply / active statuses', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('applyStatus attaches the component and records an active status', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff');

    expect(state.hasComponent(eid, StatusEffectComponent)).toBe(true);
    const active = getActiveStatuses(state, eid);
    expect(active.length).toBe(1);
    expect(active[0].defId).toBe('speed-buff');
    expect(active[0].remainingTime).toBe(10);
    expect(StatusEffectComponent.count[eid]).toBe(1);
    expect(StatusEffectComponent.version[eid]).toBeGreaterThan(0);
  });

  it('applyStatus is a no-op for an unknown def id', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'does-not-exist');
    expect(getActiveStatuses(state, eid).length).toBe(0);
  });

  it('applyStatus emits STATUS_APPLIED via the bridge system', () => {
    const eid = state.createEntity();
    const seen: unknown[] = [];
    onEvent(state, STATUS_APPLIED, (p) => seen.push(p));

    applyStatus(state, eid, 'speed-buff');
    expect(seen.length).toBe(0);
    state.step();
    expect(seen.length).toBe(1);
    expect((seen[0] as { eid: number; defId: string }).defId).toBe(
      'speed-buff'
    );
  });

  it('getActiveStatuses returns a readonly snapshot (empty for unknown entity)', () => {
    const eid = state.createEntity();
    expect(getActiveStatuses(state, eid).length).toBe(0);
  });

  it('getStatusModifiers aggregates modifiers across all active statuses', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff');
    applyStatus(state, eid, 'resolve-test');

    const mods = getStatusModifiers(state, eid);
    const speed = mods.filter((m) => m.stat === 'speed');
    expect(speed.length).toBe(2);
  });

  it('getStatusModifiers is empty when no statuses have modifiers', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'poison');
    expect(getStatusModifiers(state, eid)).toEqual([]);
  });
});

describe('StatusEffectsPlugin — tick + expire lifecycle', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('fires a tick effect each tickInterval and expires when duration elapses', () => {
    const eid = state.createEntity();
    const ticks: unknown[] = [];
    const expired: unknown[] = [];
    onEvent(state, 'status:damage', (p) => ticks.push(p));
    onEvent(state, STATUS_EXPIRED, (p) => expired.push(p));

    applyStatus(state, eid, 'poison');

    for (let i = 0; i < 3; i++) state.step(1);
    expect(ticks.length).toBe(3);
    expect(expired.length).toBe(0);
    expect(getActiveStatuses(state, eid).length).toBe(1);

    for (let i = 0; i < 7; i++) state.step(1);
    expect(ticks.length).toBe(10);
    expect(expired.length).toBe(1);
    expect(getActiveStatuses(state, eid).length).toBe(0);
    expect(StatusEffectComponent.count[eid]).toBe(0);
  });

  it('removes modifiers when the status expires', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff');
    expect(getStatusModifiers(state, eid).length).toBe(1);

    for (let i = 0; i < 10; i++) state.step(1);

    expect(getStatusModifiers(state, eid).length).toBe(0);
  });

  it('heal-over-time ticks at its configured interval', () => {
    const eid = state.createEntity();
    const heals: unknown[] = [];
    onEvent(state, 'status:heal', (p) => heals.push(p));

    applyStatus(state, eid, 'heal-over-time');
    state.step(1);
    expect(heals.length).toBe(0);
    state.step(1);
    expect(heals.length).toBe(1);
    state.step(1);
    expect(heals.length).toBe(1);
    state.step(1);
    expect(heals.length).toBe(2);
  });
});

describe('StatusEffectsPlugin — cancel', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('cancelStatus removes a single status and emits STATUS_CANCELLED', () => {
    const eid = state.createEntity();
    const cancelled: unknown[] = [];
    onEvent(state, STATUS_CANCELLED, (p) => cancelled.push(p));

    applyStatus(state, eid, 'speed-buff');
    cancelStatus(state, eid, 'speed-buff');

    expect(getActiveStatuses(state, eid).length).toBe(0);
    expect(StatusEffectComponent.count[eid]).toBe(0);
    state.step();
    expect(cancelled.length).toBe(1);
    expect((cancelled[0] as { defId: string }).defId).toBe('speed-buff');
  });

  it('cancelStatus removes modifiers immediately', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff');
    expect(getStatusModifiers(state, eid).length).toBe(1);

    cancelStatus(state, eid, 'speed-buff');
    expect(getStatusModifiers(state, eid).length).toBe(0);
  });

  it('cancelStatus on an unknown def id is a no-op', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff');
    cancelStatus(state, eid, 'not-applied');
    expect(getActiveStatuses(state, eid).length).toBe(1);
  });
});

describe('StatusEffectsPlugin — stack modes', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('replace: re-applying the same status resets duration to def.duration', () => {
    const eid = state.createEntity();
    applyStatus(state, eid, 'speed-buff'); // duration 10
    state.step(4); // remaining ~6

    applyStatus(state, eid, 'speed-buff');
    const active = getActiveStatuses(state, eid);
    expect(active.length).toBe(1);
    expect(active[0].remainingTime).toBe(10);
  });

  it('stack: re-applying accumulates remaining duration', () => {
    getDataRegistry(state).register<StatusEffectDef>(
      STATUS_KIND,
      'stackable',
      def('stackable', { duration: 5 })
    );
    const eid = state.createEntity();
    applyStatus(state, eid, 'stackable');
    applyStatus(state, eid, 'stackable', { stackMode: 'stack' });

    const active = getActiveStatuses(state, eid);
    expect(active.length).toBe(1);
    expect(active[0].remainingTime).toBe(10);
  });

  it('max: re-applying caps remaining duration at def.duration', () => {
    getDataRegistry(state).register<StatusEffectDef>(
      STATUS_KIND,
      'maxable',
      def('maxable', { duration: 5 })
    );
    const eid = state.createEntity();
    applyStatus(state, eid, 'maxable');
    state.step(2);
    applyStatus(state, eid, 'maxable', { stackMode: 'max' });

    const active = getActiveStatuses(state, eid);
    expect(active.length).toBe(1);
    expect(active[0].remainingTime).toBe(5);
  });
});

describe('StatusEffectsPlugin — LIFECYCLE: entity death cancels statuses', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('CRITICAL: when an entity dies, all its statuses are cancelled (STATUS_CANCELLED) and no tick fires after death', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Health, { current: 100, max: 100 });

    const damage: unknown[] = [];
    const cancelled: unknown[] = [];
    const death: unknown[] = [];
    onEvent(state, 'status:damage', (p) => damage.push(p));
    onEvent(state, STATUS_CANCELLED, (p) => cancelled.push(p));
    onEvent(state, COMBAT_DEATH, (p) => death.push(p));

    applyStatus(state, eid, 'poison');

    state.step(1);
    const ticksBeforeDeath = damage.length;
    expect(ticksBeforeDeath).toBe(1);

    damageHealth(eid, 100);
    state.step(1);

    expect(death.length).toBe(1);
    expect((death[0] as { target: number }).target).toBe(eid);
    expect(cancelled.length).toBe(1);
    expect(getActiveStatuses(state, eid).length).toBe(0);

    const ticksAtDeath = damage.length;

    for (let i = 0; i < 15; i++) state.step(1);
    expect(damage.length).toBe(ticksAtDeath);
    expect(damage.length).toBe(ticksBeforeDeath);
  });

  it('multiple statuses are all cancelled on death', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Health, { current: 50, max: 50 });

    const cancelled: unknown[] = [];
    onEvent(state, STATUS_CANCELLED, (p) => cancelled.push(p));

    applyStatus(state, eid, 'speed-buff');
    applyStatus(state, eid, 'poison');
    applyStatus(state, eid, 'heal-over-time');
    expect(getActiveStatuses(state, eid).length).toBe(3);

    damageHealth(eid, 50);
    state.step(1);

    expect(cancelled.length).toBe(3);
    expect(getActiveStatuses(state, eid).length).toBe(0);
    expect(getStatusModifiers(state, eid).length).toBe(0);
  });

  it('only the dying entity is affected — a second entity keeps its statuses', () => {
    const a = state.createEntity();
    state.addComponent(a, Health, { current: 10, max: 10 });
    const b = state.createEntity();
    state.addComponent(b, Health, { current: 100, max: 100 });

    applyStatus(state, a, 'speed-buff');
    applyStatus(state, b, 'speed-buff');

    damageHealth(a, 10);
    state.step(1);

    expect(getActiveStatuses(state, a).length).toBe(0);
    expect(getActiveStatuses(state, b).length).toBe(1);
  });
});

describe('StatusEffectsPlugin — destroyed entity cleanup', () => {
  let state: State;

  beforeEach(() => {
    state = newStateWithPlugins();
    registerDefs(state);
  });

  it('destroying an entity clears its active statuses (no leak, no tick)', () => {
    const eid = state.createEntity();
    const damage: unknown[] = [];
    onEvent(state, 'status:damage', (p) => damage.push(p));

    applyStatus(state, eid, 'poison');
    state.step(1);
    const before = damage.length;

    state.destroyEntity(eid);
    for (let i = 0; i < 5; i++) state.step(1);
    expect(damage.length).toBe(before);
  });
});
