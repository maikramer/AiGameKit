import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { MonoBehaviour } from '../../../../src/plugins/entity-script/components';
import {
  coerceMonoBehaviourModule,
  deleteScriptFile,
  getCachedMonoBehaviourModule,
  getScriptFile,
  registerEntityScripts,
  setCachedMonoBehaviourModule,
  setScriptFile,
} from '../../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

describe('entity-script onDestroy', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  function createScriptedEntity(
    file: string,
    mod: { start?: () => void; update?: () => void; onDestroy?: () => void }
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled: 1 });
    setScriptFile(state, eid, file);

    const globKey = `./scripts/${file}`;
    registerEntityScripts(state, { [globKey]: () => Promise.resolve(mod) });
    setCachedMonoBehaviourModule(state, globKey, mod);

    MonoBehaviour.ready[eid] = 1;

    return eid;
  }

  function registerDestroyCallback(eid: number, globKey: string): void {
    state.onDestroy(eid, () => {
      const mod = getCachedMonoBehaviourModule(state, globKey);
      if (mod?.onDestroy) {
        mod.onDestroy({
          state,
          entity: eid,
          object3d: null,
          deltaTime: 0,
        });
      }
      deleteScriptFile(state, eid);
    });
  }

  it('fires onDestroy when entity is destroyed', () => {
    let fired = false;
    const eid = createScriptedEntity('test.ts', {
      onDestroy: () => {
        fired = true;
      },
    });

    registerDestroyCallback(eid, './scripts/test.ts');
    state.destroyEntity(eid);
    expect(fired).toBe(true);
  });

  it('fires onDestroy before entity is removed from world (component still accessible)', () => {
    let componentValue: number | null = null;
    const eid = createScriptedEntity('test.ts', {
      onDestroy: () => {
        componentValue = MonoBehaviour.ready[eid];
      },
    });

    registerDestroyCallback(eid, './scripts/test.ts');
    state.destroyEntity(eid);
    expect(componentValue).toBe(1);
  });

  it('provides EntityScriptContext in onDestroy callback', () => {
    let receivedState: State | null = null;
    let receivedEntity: number | null = null;

    const eid = createScriptedEntity('test.ts', {
      onDestroy: (ctx) => {
        receivedState = ctx.state;
        receivedEntity = ctx.entity;
      },
    });

    registerDestroyCallback(eid, './scripts/test.ts');
    state.destroyEntity(eid);
    expect(receivedState).toBe(state);
    expect(receivedEntity).toBe(eid);
  });

  it('does not crash for entity with no onDestroy method', () => {
    const eid = createScriptedEntity('test.ts', {
      start: () => {},
      update: () => {},
    });

    registerDestroyCallback(eid, './scripts/test.ts');
    expect(() => state.destroyEntity(eid)).not.toThrow();
  });

  it('calls deleteScriptFile after onDestroy', () => {
    const order: string[] = [];
    const eid = createScriptedEntity('test.ts', {
      onDestroy: () => {
        order.push('onDestroy');
      },
    });

    state.onDestroy(eid, () => {
      const globKey = './scripts/test.ts';
      const mod = getCachedMonoBehaviourModule(state, globKey);
      if (mod?.onDestroy) {
        mod.onDestroy({
          state,
          entity: eid,
          object3d: null,
          deltaTime: 0,
        });
      }
      order.push('before-delete');
      deleteScriptFile(state, eid);
      order.push('after-delete');
    });

    state.destroyEntity(eid);
    expect(getScriptFile(state, eid)).toBeUndefined();
    expect(order).toEqual(['onDestroy', 'before-delete', 'after-delete']);
  });

  it('coerceEntityScriptModule extracts onDestroy from loaded module', () => {
    const onDestroy = () => {};
    const mod = coerceMonoBehaviourModule({
      start: () => {},
      onDestroy,
    });
    expect(mod?.onDestroy).toBe(onDestroy);
  });

  it('coerceEntityScriptModule returns null if only onDestroy is present (no start/update)', () => {
    const mod = coerceMonoBehaviourModule({
      onDestroy: () => {},
    });
    expect(mod).toBeNull();
  });
});
