import type { Adapter, Plugin } from '../../core';
import { Text3dModel } from './components';
import { text3dRecipe } from './recipes';
import { Text3dLoadSystem, Text3dCleanupSystem, setText3dUrl } from './systems';

export const Text3dPlugin: Plugin = {
  recipes: [text3dRecipe],
  systems: [Text3dLoadSystem, Text3dCleanupSystem],
  components: {
    text3dModel: Text3dModel,
  },
  config: {
    adapters: {
      text3dModel: {
        url: ((entity, value, _state) => {
          setText3dUrl(entity, value as string);
          Text3dModel.pending[entity] = 1;
        }) as Adapter,
      },
    },
    defaults: {
      text3dModel: {
        pending: 0,
        scale: 1,
        tint: 0,
      },
    },
  },
};
