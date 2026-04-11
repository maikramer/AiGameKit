import type { Adapter, Plugin } from '../../core';
import { EntityScript } from './components';
import { setScriptFile } from './context';
import { entityScriptRecipe } from './recipes';
import { EntityScriptFixedUpdateSystem, EntityScriptLateUpdateSystem, EntityScriptSystem } from './system';

const scriptFileAdapter: Adapter = (entity, value, state) => {
  setScriptFile(state, entity, value);
};

export const EntityScriptPlugin: Plugin = {
  recipes: [entityScriptRecipe],
  systems: [EntityScriptFixedUpdateSystem, EntityScriptSystem, EntityScriptLateUpdateSystem],
  components: {
    entityScript: EntityScript,
  },
  config: {
    defaults: {
      entityScript: {
        ready: 0,
        enabled: 1,
      },
    },
    shorthands: {
      entityScript: {
        script: 'file',
      },
    },
    adapters: {
      entityScript: {
        file: scriptFileAdapter,
      },
    },
  },
};
