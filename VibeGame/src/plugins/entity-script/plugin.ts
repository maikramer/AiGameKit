import type { Adapter, Plugin } from '../../core';
import { EntityScript } from './components';
import { setScriptFile } from './context';
import { entityScriptRecipe } from './recipes';
import { EntityScriptSystem } from './system';

const scriptFileAdapter: Adapter = (entity, value, state) => {
  setScriptFile(state, entity, value);
};

export const EntityScriptPlugin: Plugin = {
  recipes: [entityScriptRecipe],
  systems: [EntityScriptSystem],
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
