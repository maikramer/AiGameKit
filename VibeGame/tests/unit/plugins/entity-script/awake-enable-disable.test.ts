import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { EntityScript } from '../../../../src/plugins/entity-script/components';
import {
  coerceEntityScriptModule,
  deletePrevEnabled,
  deleteScriptFile,
  getCachedEntityScriptModule,
  getPrevEnabled,
  registerEntityScripts,
  setCachedEntityScriptModule,
  setPrevEnabled,
  setScriptFile,
} from '../../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

describe('entity-script awake/onEnable/onDisable', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  function createScriptedEntity(
    file: string,
    mod: Record<string, unknown>,
    enabled = 1
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, EntityScript, { ready: 0, enabled });
    setScriptFile(state, eid, file);

    const globKey = `./scripts/${file}`;
    registerEntityScripts(state, { [globKey]: () => Promise.resolve(mod) });
    setCachedEntityScriptModule(state, globKey, mod as ReturnType<typeof coerceEntityScriptModule>);

    EntityScript.ready[eid] = 1;

    return eid;
  }

  function simulateSetup(eid: number, globKey: string): void {
    const mod = getCachedEntityScriptModule(state, globKey);
    if (!mod) return;
    const ctx = { state, entity: eid, object3d: null, deltaTime: 0 };
    if (mod.awake) mod.awake(ctx);
    const isEnabled = EntityScript.enabled[eid] === 1;
    if (isEnabled && mod.onEnable) mod.onEnable(ctx);
    if (mod.setup) mod.setup(ctx);
    setPrevEnabled(state, eid, isEnabled ? 1 : 0);
  }

  function registerDestroyCallback(eid: number, globKey: string): void {
    state.onDestroy(eid, () => {
      const mod = getCachedEntityScriptModule(state, globKey);
      if (mod) {
        const destroyCtx = { state, entity: eid, object3d: null, deltaTime: 0 };
        if (EntityScript.enabled[eid] === 1 && mod.onDisable) {
          mod.onDisable(destroyCtx);
        }
        if (mod.onDestroy) mod.onDestroy(destroyCtx);
      }
      deletePrevEnabled(state, eid);
      deleteScriptFile(state, eid);
    });
  }

  it('awake fires once on first creation', () => {
    let awakeCount = 0;
    const eid = createScriptedEntity('test.ts', {
      awake: () => { awakeCount++; },
      setup: () => {},
    });
    simulateSetup(eid, './scripts/test.ts');
    expect(awakeCount).toBe(1);
  });

  it('awake fires even if entity starts disabled', () => {
    let awakeFired = false;
    const eid = createScriptedEntity('test.ts', {
      awake: () => { awakeFired = true; },
      setup: () => {},
    }, 0);
    simulateSetup(eid, './scripts/test.ts');
    expect(awakeFired).toBe(true);
    expect(EntityScript.enabled[eid]).toBe(0);
  });

  it('lifecycle order is awake → onEnable → setup for enabled=1', () => {
    const order: string[] = [];
    const eid = createScriptedEntity('test.ts', {
      awake: () => { order.push('awake'); },
      onEnable: () => { order.push('onEnable'); },
      setup: () => { order.push('setup'); },
    });
    simulateSetup(eid, './scripts/test.ts');
    expect(order).toEqual(['awake', 'onEnable', 'setup']);
  });

  it('onEnable does NOT fire if entity created with enabled=0', () => {
    let onEnableFired = false;
    const eid = createScriptedEntity('test.ts', {
      awake: () => {},
      onEnable: () => { onEnableFired = true; },
      setup: () => {},
    }, 0);
    simulateSetup(eid, './scripts/test.ts');
    expect(onEnableFired).toBe(false);
  });

  it('toggling enabled fires onEnable on 0→1 and onDisable on 1→0', () => {
    const order: string[] = [];
    const eid = createScriptedEntity('test.ts', {
      onEnable: () => { order.push('onEnable'); },
      onDisable: () => { order.push('onDisable'); },
      update: () => {},
    });
    simulateSetup(eid, './scripts/test.ts');

    const mod = getCachedEntityScriptModule(state, './scripts/test.ts')!;
    const ctx = { state, entity: eid, object3d: null, deltaTime: 0 };

    EntityScript.enabled[eid] = 0;
    if (mod.onDisable) mod.onDisable(ctx);
    setPrevEnabled(state, eid, 0);

    EntityScript.enabled[eid] = 1;
    if (mod.onEnable) mod.onEnable(ctx);
    setPrevEnabled(state, eid, 1);

    expect(order).toEqual(['onEnable', 'onDisable', 'onEnable']);
  });

  it('destroying enabled entity fires onDisable then onDestroy', () => {
    const order: string[] = [];
    const eid = createScriptedEntity('test.ts', {
      onDisable: () => { order.push('onDisable'); },
      onDestroy: () => { order.push('onDestroy'); },
      setup: () => {},
    });
    simulateSetup(eid, './scripts/test.ts');
    registerDestroyCallback(eid, './scripts/test.ts');
    state.destroyEntity(eid);
    expect(order).toEqual(['onDisable', 'onDestroy']);
  });

  it('re-enabling does NOT fire awake again', () => {
    let awakeCount = 0;
    const eid = createScriptedEntity('test.ts', {
      awake: () => { awakeCount++; },
      onEnable: () => {},
      onDisable: () => {},
      setup: () => {},
    });
    simulateSetup(eid, './scripts/test.ts');

    const mod = getCachedEntityScriptModule(state, './scripts/test.ts')!;
    const ctx = { state, entity: eid, object3d: null, deltaTime: 0 };

    EntityScript.enabled[eid] = 0;
    if (mod.onDisable) mod.onDisable(ctx);
    setPrevEnabled(state, eid, 0);

    EntityScript.enabled[eid] = 1;
    if (mod.onEnable) mod.onEnable(ctx);
    setPrevEnabled(state, eid, 1);

    expect(awakeCount).toBe(1);
  });

  it('coerceEntityScriptModule extracts awake, onEnable, onDisable', () => {
    const awake = () => {};
    const onEnable = () => {};
    const onDisable = () => {};
    const mod = coerceEntityScriptModule({
      setup: () => {},
      awake,
      onEnable,
      onDisable,
    });
    expect(mod?.awake).toBe(awake);
    expect(mod?.onEnable).toBe(onEnable);
    expect(mod?.onDisable).toBe(onDisable);
  });

  it('coerceEntityScriptModule returns null if only lifecycle hooks present (no setup/update)', () => {
    const mod = coerceEntityScriptModule({
      awake: () => {},
      onEnable: () => {},
      onDisable: () => {},
    });
    expect(mod).toBeNull();
  });

  it('prevEnabled is cleaned up after entity destruction', () => {
    const eid = createScriptedEntity('test.ts', { setup: () => {} });
    simulateSetup(eid, './scripts/test.ts');
    expect(getPrevEnabled(state, eid)).toBe(1);

    registerDestroyCallback(eid, './scripts/test.ts');
    state.destroyEntity(eid);
    expect(getPrevEnabled(state, eid)).toBeUndefined();
  });
});
