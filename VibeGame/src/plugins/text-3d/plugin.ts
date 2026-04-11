import type { Adapter, Plugin } from '../../core';
import { TextMesh } from './components';
import { text3dRecipe } from './recipes';
import { Text3dLoadSystem, Text3dCleanupSystem, setText3dUrl } from './systems';

export const Text3dPlugin: Plugin = {
  recipes: [text3dRecipe],
  systems: [Text3dLoadSystem, Text3dCleanupSystem],
  components: {
    textMesh: TextMesh,
  },
  config: {
    adapters: {
      textMesh: {
        url: ((entity, value, _state) => {
          setText3dUrl(entity, value as string);
          TextMesh.pending[entity] = 1;
        }) as Adapter,
      },
    },
    defaults: {
      textMesh: {
        pending: 0,
        scale: 1,
        tint: 0,
      },
    },
  },
};
