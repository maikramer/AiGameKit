import type { Adapter, Plugin } from '../../core';
import { TextMesh } from './components';
import { text3dRecipe } from './recipes';
import { Text3dLoadSystem, Text3dCleanupSystem, setText3dUrl } from './systems';

export const Text3dPlugin: Plugin = {
  recipes: [text3dRecipe],
  systems: [Text3dLoadSystem, Text3dCleanupSystem],
  components: {
    text3dModel: TextMesh,
  },
  config: {
    adapters: {
      text3dModel: {
        url: ((entity, value, _state) => {
          setText3dUrl(entity, value as string);
          TextMesh.pending[entity] = 1;
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
