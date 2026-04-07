import type { Adapter, Plugin } from '../../core';
import { GltfPending, GltfPhysicsPending } from './components';
import { setGltfUrl } from './context';
import { GltfDynamicPhysicsSystem } from './gltf-dynamic-system';
import { gltfDynamicRecipe, gltfLoadRecipe } from './recipes';
import { GltfXmlLoadSystem } from './systems';

export const GltfXmlPlugin: Plugin = {
  recipes: [gltfLoadRecipe, gltfDynamicRecipe],
  systems: [GltfXmlLoadSystem, GltfDynamicPhysicsSystem],
  components: {
    gltfPending: GltfPending,
    gltfPhysicsPending: GltfPhysicsPending,
  },
  config: {
    adapters: {
      gltfPending: {
        url: ((entity, value, state) => {
          setGltfUrl(state, entity, value);
        }) as Adapter,
      },
    },
    defaults: {
      gltfPending: {
        loaded: 0,
      },
      gltfPhysicsPending: {
        ready: 0,
        colliderMargin: 0.02,
        mass: 1,
        friction: 0.5,
        restitution: 0,
      },
    },
  },
};
