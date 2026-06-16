import { beforeEach, describe, expect, it } from 'bun:test';
import {
  COMBAT_DAMAGED,
  COMBAT_DEATH,
  ECONOMY_SPENT,
  EventBus,
  EventBusCleanupSystem,
  PROGRESSION_LEVEL_UP,
  RpgCoreEventsPlugin,
  State,
  emitEvent,
  getEventBus,
  onEvent,
} from 'vibegame';

describe('EventBus / rpg-core events', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RpgCoreEventsPlugin);
  });

  describe('emit + on', () => {
    it('handler fires synchronously with payload', () => {
      const calls: unknown[] = [];
      onEvent(state, COMBAT_DAMAGED, (p) => {
        calls.push(p);
      });
      emitEvent(state, COMBAT_DAMAGED, { target: 1, amount: 10 });

      expect(calls.length).toBe(1);
      expect(calls[0]).toEqual({ target: 1, amount: 10 });
    });

    it('multiple handlers fire in subscription order', () => {
      const order: string[] = [];
      onEvent(state, 'test', () => order.push('a'));
      onEvent(state, 'test', () => order.push('b'));
      emitEvent(state, 'test', null);

      expect(order).toEqual(['a', 'b']);
    });

    it('emit with no subscribers is a no-op', () => {
      expect(() => emitEvent(state, 'unheard', { x: 1 })).not.toThrow();
    });

    it('a handler error does not block sibling handlers', () => {
      const seen: string[] = [];
      onEvent(state, 'resilient', () => {
        seen.push('boom');
        throw new Error('boom');
      });
      onEvent(state, 'resilient', () => seen.push('after'));

      emitEvent(state, 'resilient', null);

      expect(seen).toEqual(['boom', 'after']);
    });
  });

  describe('off', () => {
    it('cancels a subscription so it no longer fires', () => {
      let calls = 0;
      const subId = onEvent(state, 'test', () => {
        calls++;
      });
      getEventBus(state).off(subId);
      emitEvent(state, 'test', null);

      expect(calls).toBe(0);
    });

    it('off on unknown id is a no-op', () => {
      const bus = getEventBus(state);
      expect(() => bus.off(9999)).not.toThrow();
    });

    it('cancelling one handler keeps the rest', () => {
      let a = 0;
      let b = 0;
      const idA = onEvent(state, 'ev', () => {
        a++;
      });
      onEvent(state, 'ev', () => {
        b++;
      });
      getEventBus(state).off(idA);
      emitEvent(state, 'ev', null);

      expect(a).toBe(0);
      expect(b).toBe(1);
    });
  });

  describe('once', () => {
    it('fires exactly once then auto-unsubscribes', () => {
      let calls = 0;
      const bus = getEventBus(state);
      bus.once('once-test', () => {
        calls++;
      });

      emitEvent(state, 'once-test', null);
      emitEvent(state, 'once-test', null);

      expect(calls).toBe(1);
    });

    it('once passes payload to the handler', () => {
      const payloads: unknown[] = [];
      const bus = getEventBus(state);
      bus.once('once-payload', (p) => payloads.push(p));

      emitEvent(state, 'once-payload', { v: 42 });
      emitEvent(state, 'once-payload', { v: 99 });

      expect(payloads).toEqual([{ v: 42 }]);
    });
  });

  describe('clear', () => {
    it('clears a single event', () => {
      let a = 0;
      let b = 0;
      onEvent(state, 'evA', () => {
        a++;
      });
      onEvent(state, 'evB', () => {
        b++;
      });

      const bus = getEventBus(state);
      bus.clear('evA');
      emitEvent(state, 'evA', null);
      emitEvent(state, 'evB', null);

      expect(a).toBe(0);
      expect(b).toBe(1);
    });

    it('clears all events when no arg given', () => {
      let a = 0;
      let b = 0;
      onEvent(state, 'evA', () => {
        a++;
      });
      onEvent(state, 'evB', () => {
        b++;
      });

      getEventBus(state).clear();
      emitEvent(state, 'evA', null);
      emitEvent(state, 'evB', null);

      expect(a).toBe(0);
      expect(b).toBe(0);
    });
  });

  describe('per-State isolation', () => {
    it('getEventBus returns the same instance for a given state', () => {
      expect(getEventBus(state)).toBe(getEventBus(state));
    });

    it('different states get different buses', () => {
      const other = new State();
      expect(getEventBus(state)).not.toBe(getEventBus(other));
    });

    it('EventBus can be constructed standalone', () => {
      const bus = new EventBus();
      let calls = 0;
      bus.on('x', () => {
        calls++;
      });
      bus.emit('x', null);
      expect(calls).toBe(1);
    });
  });

  describe('standard event name constants', () => {
    it('exports documented const strings', () => {
      expect(COMBAT_DAMAGED).toBe('combat:damaged');
      expect(COMBAT_DEATH).toBe('combat:death');
      expect(ECONOMY_SPENT).toBe('economy:spent');
      expect(PROGRESSION_LEVEL_UP).toBe('progression:level-up');
    });
  });

  describe('EventBusCleanupSystem', () => {
    it('removes subscriptions whose entityRef was destroyed', () => {
      state.registerSystem(EventBusCleanupSystem);
      const eid = state.createEntity();

      let calls = 0;
      onEvent(
        state,
        'bound',
        () => {
          calls++;
        },
        { entityRef: eid }
      );

      emitEvent(state, 'bound', null);
      expect(calls).toBe(1);

      state.destroyEntity(eid);
      state.step();

      emitEvent(state, 'bound', null);
      expect(calls).toBe(1);
    });

    it('keeps subscriptions for entities still alive', () => {
      state.registerSystem(EventBusCleanupSystem);
      const eid = state.createEntity();

      let calls = 0;
      onEvent(
        state,
        'bound',
        () => {
          calls++;
        },
        { entityRef: eid }
      );

      state.step();
      emitEvent(state, 'bound', null);

      expect(calls).toBe(1);
      expect(state.exists(eid)).toBe(true);
    });

    it('keeps subscriptions without an entityRef', () => {
      state.registerSystem(EventBusCleanupSystem);
      let calls = 0;
      onEvent(state, 'free', () => {
        calls++;
      });

      state.step();
      emitEvent(state, 'free', null);

      expect(calls).toBe(1);
    });

    it('destroys selectively: keeps alive-ref, removes dead-ref', () => {
      state.registerSystem(EventBusCleanupSystem);
      const alive = state.createEntity();
      const dead = state.createEntity();

      let aliveCalls = 0;
      let deadCalls = 0;
      onEvent(
        state,
        'ev',
        () => {
          aliveCalls++;
        },
        { entityRef: alive }
      );
      onEvent(
        state,
        'ev',
        () => {
          deadCalls++;
        },
        { entityRef: dead }
      );

      state.destroyEntity(dead);
      state.step();

      emitEvent(state, 'ev', null);

      expect(aliveCalls).toBe(1);
      expect(deadCalls).toBe(0);
    });
  });
});
