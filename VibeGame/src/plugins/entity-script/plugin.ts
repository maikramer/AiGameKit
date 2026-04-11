import type { Adapter, Plugin } from '../../core';
import { CoroutineRunnerSystem } from '../../core/ecs/coroutines';
import { MonoBehaviour } from './components';
import { setScriptFile } from './context';
import { entityScriptRecipe } from './recipes';
import {
  EntityScriptCollisionBridgeSystem,
  EntityScriptFixedUpdateSystem,
  EntityScriptLateUpdateSystem,
  EntityScriptSystem,
} from './system';

const scriptFileAdapter: Adapter = (entity, value, state) => {
  setScriptFile(state, entity, value);
};

export const EntityScriptPlugin: Plugin = {
  recipes: [entityScriptRecipe],
  systems: [
    CoroutineRunnerSystem,
    EntityScriptCollisionBridgeSystem,
    EntityScriptFixedUpdateSystem,
    EntityScriptSystem,
    EntityScriptLateUpdateSystem,
  ],
  components: {
    monoBehaviour: MonoBehaviour,
  },
  config: {
    defaults: {
      monoBehaviour: {
        ready: 0,
        enabled: 1,
      },
    },
    shorthands: {
      monoBehaviour: {
        script: 'file',
      },
    },
    adapters: {
      monoBehaviour: {
        file: scriptFileAdapter,
      },
    },
  },
};
