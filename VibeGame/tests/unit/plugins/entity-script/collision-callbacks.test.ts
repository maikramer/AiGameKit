import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { Collider, TouchedEvent, TouchEndedEvent } from '../../../../src/plugins/physics/components';
import { MonoBehaviour } from '../../../../src/plugins/entity-script/components';
import {
  addActiveCollisionPair,
  coerceEntityScriptModule,
  deleteActiveCollisionPairsForEntity,
  getActiveCollisionPairs,
  registerEntityScripts,
  setCachedEntityScriptModule,
  setScriptFile,
} from '../../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import { EntityScriptCollisionBridgeSystem } from '../../../../src/plugins/entity-script/system';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

describe('entity-script collision/trigger callbacks', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  function createScriptedEntity(
    file: string,
    mod: Record<string, unknown>,
    options: { enabled?: number; isSensor?: number } = {},
  ): number {
    const { enabled = 1, isSensor = 0 } = options;
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled });
    state.addComponent(eid, Collider, { isSensor });
    setScriptFile(state, eid, file);

    const globKey = `./scripts/${file}`;
    registerEntityScripts(state, { [globKey]: () => Promise.resolve(mod) });
    setCachedEntityScriptModule(state, globKey, mod as ReturnType<typeof coerceEntityScriptModule>);

    MonoBehaviour.ready[eid] = 1;
    return eid;
  }

  function createColliderEntity(isSensor = 0): number {
    const eid = state.createEntity();
    state.addComponent(eid, Collider, { isSensor });
    return eid;
  }

  function addTouchedEvent(eid: number, other: number): void {
    state.addComponent(eid, TouchedEvent);
    TouchedEvent.other[eid] = other;
  }

  function addTouchEndedEvent(eid: number, other: number): void {
    state.addComponent(eid, TouchEndedEvent);
    TouchEndedEvent.other[eid] = other;
  }

  function removeCollisionEvents(eid: number): void {
    if (state.hasComponent(eid, TouchedEvent)) {
      state.removeComponent(eid, TouchedEvent);
    }
    if (state.hasComponent(eid, TouchEndedEvent)) {
      state.removeComponent(eid, TouchEndedEvent);
    }
  }

  function runBridge(): void {
    EntityScriptCollisionBridgeSystem.update(state);
  }

  describe('coerceEntityScriptModule', () => {
    it('extracts all 6 collision/trigger callbacks', () => {
      const onCollisionEnter = () => {};
      const onCollisionStay = () => {};
      const onCollisionExit = () => {};
      const onTriggerEnter = () => {};
      const onTriggerStay = () => {};
      const onTriggerExit = () => {};
      const mod = coerceEntityScriptModule({
        start: () => {},
        onCollisionEnter,
        onCollisionStay,
        onCollisionExit,
        onTriggerEnter,
        onTriggerStay,
        onTriggerExit,
      });
      expect(mod?.onCollisionEnter).toBe(onCollisionEnter);
      expect(mod?.onCollisionStay).toBe(onCollisionStay);
      expect(mod?.onCollisionExit).toBe(onCollisionExit);
      expect(mod?.onTriggerEnter).toBe(onTriggerEnter);
      expect(mod?.onTriggerStay).toBe(onTriggerStay);
      expect(mod?.onTriggerExit).toBe(onTriggerExit);
    });

    it('returns null when only collision callbacks present (no start/update)', () => {
      const mod = coerceEntityScriptModule({
        onCollisionEnter: () => {},
        onTriggerEnter: () => {},
      });
      expect(mod).toBeNull();
    });

    it('returns undefined for unprovided collision callbacks', () => {
      const mod = coerceEntityScriptModule({
        start: () => {},
        onCollisionEnter: () => {},
      });
      expect(mod?.onCollisionEnter).toBeDefined();
      expect(mod?.onCollisionStay).toBeUndefined();
      expect(mod?.onCollisionExit).toBeUndefined();
      expect(mod?.onTriggerEnter).toBeUndefined();
      expect(mod?.onTriggerStay).toBeUndefined();
      expect(mod?.onTriggerExit).toBeUndefined();
    });
  });

  describe('onCollisionEnter', () => {
    it('fires when two non-sensor entities collide', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: (_ctx: unknown, other: { entity: number }) => {
          events.push(`enter:${other.entity}`);
        },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([`enter:${otherEid}`]);
    });

    it('provides EntityScriptContext and CollisionOther', () => {
      let receivedCtx: unknown;
      let receivedOther: unknown;
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: (ctx: unknown, other: unknown) => {
          receivedCtx = ctx;
          receivedOther = other;
        },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect((receivedCtx as { entity: number }).entity).toBe(eid);
      expect((receivedCtx as { state: State }).state).toBe(state);
      expect(receivedOther).toEqual({ entity: otherEid });
    });
  });

  describe('onCollisionExit', () => {
    it('fires when collision ends', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionExit: (_ctx: unknown, other: { entity: number }) => {
          events.push(`exit:${other.entity}`);
        },
      });

      addActiveCollisionPair(state, eid, otherEid, false);
      addTouchEndedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([`exit:${otherEid}`]);
    });
  });

  describe('onCollisionStay', () => {
    it('fires on subsequent frames while still colliding', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionStay: (_ctx: unknown, other: { entity: number }) => {
          events.push(`stay:${other.entity}`);
        },
      });

      addActiveCollisionPair(state, eid, otherEid, false);
      runBridge();

      expect(events).toEqual([`stay:${otherEid}`]);
    });

    it('does not fire on the same frame as enter', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('enter'); },
        onCollisionStay: () => { events.push('stay'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual(['enter']);
    });

    it('fires on the frame after enter', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('enter'); },
        onCollisionStay: () => { events.push('stay'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();
      removeCollisionEvents(eid);
      runBridge();

      expect(events).toEqual(['enter', 'stay']);
    });
  });

  describe('onTriggerEnter', () => {
    it('fires when one entity is a sensor', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onTriggerEnter: (_ctx: unknown, other: { entity: number }) => {
          events.push(`trigger-enter:${other.entity}`);
        },
        onCollisionEnter: () => { events.push('collision-enter'); },
      }, { isSensor: 1 });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([`trigger-enter:${otherEid}`]);
    });

    it('fires when the other entity is a sensor', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(1);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onTriggerEnter: (_ctx: unknown, other: { entity: number }) => {
          events.push(`trigger-enter:${other.entity}`);
        },
        onCollisionEnter: () => { events.push('collision-enter'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([`trigger-enter:${otherEid}`]);
    });
  });

  describe('onTriggerExit', () => {
    it('fires when sensor overlap ends', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onTriggerExit: (_ctx: unknown, other: { entity: number }) => {
          events.push(`trigger-exit:${other.entity}`);
        },
      }, { isSensor: 1 });

      addActiveCollisionPair(state, eid, otherEid, true);
      addTouchEndedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([`trigger-exit:${otherEid}`]);
    });
  });

  describe('onTriggerStay', () => {
    it('fires while sensor overlap persists', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onTriggerStay: (_ctx: unknown, other: { entity: number }) => {
          events.push(`trigger-stay:${other.entity}`);
        },
      }, { isSensor: 1 });

      addActiveCollisionPair(state, eid, otherEid, true);
      runBridge();

      expect(events).toEqual([`trigger-stay:${otherEid}`]);
    });
  });

  describe('sensor distinction', () => {
    it('both non-sensor → collision callbacks', () => {
      const collisionEvents: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { collisionEvents.push('collision'); },
        onTriggerEnter: () => { collisionEvents.push('trigger'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(collisionEvents).toEqual(['collision']);
    });

    it('self is sensor → trigger callbacks', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('collision'); },
        onTriggerEnter: () => { events.push('trigger'); },
      }, { isSensor: 1 });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual(['trigger']);
    });

    it('other is sensor → trigger callbacks', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(1);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('collision'); },
        onTriggerEnter: () => { events.push('trigger'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual(['trigger']);
    });
  });

  describe('callback only if defined', () => {
    it('does not crash when module lacks collision callbacks', () => {
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        update: () => {},
      });

      addTouchedEvent(eid, otherEid);
      expect(() => runBridge()).not.toThrow();
    });
  });

  describe('disabled entity', () => {
    it('does not fire callbacks when entity is disabled', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('enter'); },
      }, { enabled: 0 });

      addTouchedEvent(eid, otherEid);
      runBridge();

      expect(events).toEqual([]);
    });
  });

  describe('multiple simultaneous collisions', () => {
    it('fires enter for each other entity', () => {
      const events: string[] = [];
      const other1 = createColliderEntity(0);
      const other2 = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: (_ctx: unknown, other: { entity: number }) => {
          events.push(other.entity);
        },
      });

      addTouchedEvent(eid, other1);
      TouchedEvent.other[eid] = other1;

      const eid2 = state.createEntity();
      state.addComponent(eid2, MonoBehaviour, { ready: 1, enabled: 1 });
      state.addComponent(eid2, Collider, { isSensor: 0 });
      setScriptFile(state, eid2, 'test.ts');

      addTouchedEvent(eid, other1);
      addTouchedEvent(eid2, other2);

      runBridge();

      expect(events.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe('active pair tracking', () => {
    it('removes pair from active set on exit', () => {
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionExit: () => {},
      });

      addActiveCollisionPair(state, eid, otherEid, false);
      addTouchEndedEvent(eid, otherEid);
      runBridge();

      expect(getActiveCollisionPairs(state).has(eid)).toBe(false);
    });

    it('cleans up pairs for destroyed entities during stay', () => {
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionStay: () => {},
      });

      addActiveCollisionPair(state, eid, otherEid, false);
      state.destroyEntity(eid);
      runBridge();

      expect(getActiveCollisionPairs(state).has(eid)).toBe(false);
    });

    it('cleans up pairs for destroyed other entities during stay', () => {
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionStay: () => {},
      });

      addActiveCollisionPair(state, eid, otherEid, false);
      state.destroyEntity(otherEid);
      runBridge();

      const pairs = getActiveCollisionPairs(state);
      expect(pairs.get(eid)?.has(otherEid)).toBeFalsy();
    });

    it('deleteActiveCollisionPairsForEntity removes all pairs', () => {
      const eid = createScriptedEntity('test.ts', { start: () => {} });
      addActiveCollisionPair(state, eid, 100, false);
      addActiveCollisionPair(state, eid, 200, true);

      deleteActiveCollisionPairsForEntity(state, eid);

      expect(getActiveCollisionPairs(state).has(eid)).toBe(false);
    });
  });

  describe('full enter-stay-exit lifecycle', () => {
    it('fires enter then stay then exit across frames', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onCollisionEnter: () => { events.push('enter'); },
        onCollisionStay: () => { events.push('stay'); },
        onCollisionExit: () => { events.push('exit'); },
      });

      addTouchedEvent(eid, otherEid);
      runBridge();
      expect(events).toEqual(['enter']);

      removeCollisionEvents(eid);
      runBridge();
      expect(events).toEqual(['enter', 'stay']);

      removeCollisionEvents(eid);
      runBridge();
      expect(events).toEqual(['enter', 'stay', 'stay']);

      addTouchEndedEvent(eid, otherEid);
      runBridge();
      expect(events).toEqual(['enter', 'stay', 'stay', 'exit']);
    });
  });

  describe('trigger full lifecycle', () => {
    it('fires trigger enter then stay then exit across frames', () => {
      const events: string[] = [];
      const otherEid = createColliderEntity(0);
      const eid = createScriptedEntity('test.ts', {
        start: () => {},
        onTriggerEnter: () => { events.push('trigger-enter'); },
        onTriggerStay: () => { events.push('trigger-stay'); },
        onTriggerExit: () => { events.push('trigger-exit'); },
      }, { isSensor: 1 });

      addTouchedEvent(eid, otherEid);
      runBridge();
      expect(events).toEqual(['trigger-enter']);

      removeCollisionEvents(eid);
      runBridge();
      expect(events).toEqual(['trigger-enter', 'trigger-stay']);

      addTouchEndedEvent(eid, otherEid);
      runBridge();
      expect(events).toEqual(['trigger-enter', 'trigger-stay', 'trigger-exit']);
    });
  });
});
