import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { MonoBehaviour } from '../../../../src/plugins/entity-script/components';
import {
  coerceMonoBehaviourModule,
  getCachedMonoBehaviourModule,
  registerEntityScripts,
  setCachedMonoBehaviourModule,
  setScriptFile,
} from '../../../../src/plugins/entity-script/context';
import {
  EntityScriptFixedUpdateSystem,
  EntityScriptLateUpdateSystem,
  EntityScriptSystem,
} from '../../../../src/plugins/entity-script/system';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

describe('entity-script fixedUpdate / lateUpdate', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  function createScriptedEntity(
    file: string,
    mod: Record<string, unknown>,
    enabled = 1,
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled });
    setScriptFile(state, eid, file);

    const globKey = `./scripts/${file}`;
    registerEntityScripts(state, { [globKey]: () => Promise.resolve(mod) });
    setCachedMonoBehaviourModule(state, globKey, mod as ReturnType<typeof coerceMonoBehaviourModule>);

    MonoBehaviour.ready[eid] = 1;

    return eid;
  }

  it('update fires each time EntityScriptSystem.update runs', () => {
    let updateCount = 0;
    createScriptedEntity('test.ts', {
      update: () => { updateCount++; },
    });

    EntityScriptSystem.update(state);
    EntityScriptSystem.update(state);
    EntityScriptSystem.update(state);

    expect(updateCount).toBe(3);
  });

  it('update does NOT fire when entity is disabled', () => {
    let updateCount = 0;
    createScriptedEntity('test.ts', {
      update: () => { updateCount++; },
    }, 0);

    EntityScriptSystem.update(state);
    EntityScriptSystem.update(state);

    expect(updateCount).toBe(0);
  });

  it('fixedUpdate fires when entity is enabled and ready', () => {
    let fixedCount = 0;
    createScriptedEntity('test.ts', {
      fixedUpdate: () => { fixedCount++; },
      update: () => {},
    });

    EntityScriptFixedUpdateSystem.update(state);
    EntityScriptFixedUpdateSystem.update(state);

    expect(fixedCount).toBe(2);
  });

  it('fixedUpdate does NOT fire when entity is disabled', () => {
    let fixedCount = 0;
    createScriptedEntity('test.ts', {
      fixedUpdate: () => { fixedCount++; },
      update: () => {},
    }, 0);

    EntityScriptFixedUpdateSystem.update(state);

    expect(fixedCount).toBe(0);
  });

  it('fixedUpdate does NOT fire when entity is not ready', () => {
    let fixedCount = 0;
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled: 1 });
    setScriptFile(state, eid, 'notready.ts');
    registerEntityScripts(state, { './scripts/notready.ts': () => Promise.resolve({ fixedUpdate: () => { fixedCount++; }, update: () => {} }) });
    setCachedMonoBehaviourModule(state, './scripts/notready.ts', coerceMonoBehaviourModule({ fixedUpdate: () => { fixedCount++; }, update: () => {} })!);

    EntityScriptFixedUpdateSystem.update(state);

    expect(fixedCount).toBe(0);
  });

  it('lateUpdate fires when entity is enabled and ready', () => {
    let lateCount = 0;
    createScriptedEntity('test.ts', {
      lateUpdate: () => { lateCount++; },
      update: () => {},
    });

    EntityScriptLateUpdateSystem.update(state);
    EntityScriptLateUpdateSystem.update(state);

    expect(lateCount).toBe(2);
  });

  it('lateUpdate does NOT fire when entity is disabled', () => {
    let lateCount = 0;
    createScriptedEntity('test.ts', {
      lateUpdate: () => { lateCount++; },
      update: () => {},
    }, 0);

    EntityScriptLateUpdateSystem.update(state);

    expect(lateCount).toBe(0);
  });

  it('lateUpdate does NOT fire when entity is not ready', () => {
    let lateCount = 0;
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 0, enabled: 1 });
    setScriptFile(state, eid, 'notready.ts');
    registerEntityScripts(state, { './scripts/notready.ts': () => Promise.resolve({ lateUpdate: () => { lateCount++; }, update: () => {} }) });
    setCachedMonoBehaviourModule(state, './scripts/notready.ts', coerceMonoBehaviourModule({ lateUpdate: () => { lateCount++; }, update: () => {} })!);

    EntityScriptLateUpdateSystem.update(state);

    expect(lateCount).toBe(0);
  });

  it('coerceEntityScriptModule extracts fixedUpdate and lateUpdate', () => {
    const fixedUpdate = () => {};
    const lateUpdate = () => {};
    const mod = coerceMonoBehaviourModule({
      update: () => {},
      fixedUpdate,
      lateUpdate,
    });
    expect(mod?.fixedUpdate).toBe(fixedUpdate);
    expect(mod?.lateUpdate).toBe(lateUpdate);
  });

  it('fixedUpdate and lateUpdate skipped when module has no such method', () => {
    const eid = createScriptedEntity('test.ts', {
      update: () => {},
    });

    EntityScriptFixedUpdateSystem.update(state);
    EntityScriptLateUpdateSystem.update(state);

    expect(MonoBehaviour.ready[eid]).toBe(1);
  });

  it('fixedUpdate and lateUpdate skipped for entity without script file', () => {
    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 1, enabled: 1 });

    EntityScriptFixedUpdateSystem.update(state);
    EntityScriptLateUpdateSystem.update(state);

    expect(state.exists(eid)).toBe(true);
  });
});
