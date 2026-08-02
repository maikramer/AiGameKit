import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { MonoBehaviour } from '../../../../src/plugins/entity-script/components';
import {
  registerEntityScripts,
  setCachedMonoBehaviourModule,
  setScriptFile,
  setScriptRuntime,
} from '../../../../src/plugins/entity-script/context';
import { EntityScriptPlugin } from '../../../../src/plugins/entity-script/plugin';
import {
  buildContext,
  EntityScriptSystem,
} from '../../../../src/plugins/entity-script/system';
import { DistanceCull } from '../../../../src/plugins/rendering/components';
import { TransformsPlugin } from '../../../../src/plugins/transforms';

describe('entity-script skips DistanceCull.culled entities', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(EntityScriptPlugin);
  });

  it('does not call update while culled', () => {
    let updates = 0;
    const mod = {
      update: () => {
        updates += 1;
      },
    };
    const globKey = './scripts/far.ts';
    registerEntityScripts(state, { [globKey]: () => Promise.resolve(mod) });
    setCachedMonoBehaviourModule(state, globKey, mod as never);

    const eid = state.createEntity();
    state.addComponent(eid, MonoBehaviour, { ready: 1, enabled: 1 });
    setScriptFile(state, eid, 'far.ts');
    state.addComponent(eid, DistanceCull);
    DistanceCull.maxDistance[eid] = 40;
    DistanceCull.culled[eid] = 1;
    // The hot loop resolves the runtime from the setup cache; mirror setup so
    // the unculled update actually fires.
    setScriptRuntime(state, eid, {
      mod,
      ctx: buildContext(state, eid),
      file: 'far.ts',
    });

    EntityScriptSystem.update!(state);
    EntityScriptSystem.update!(state);
    expect(updates).toBe(0);

    DistanceCull.culled[eid] = 0;
    EntityScriptSystem.update!(state);
    expect(updates).toBe(1);
  });
});
