import type { Adapter, Plugin } from '../../core';
import { GltfPending, GltfPhysicsPending } from './components';
import { setGltfUrl } from './context';
import { GltfDynamicPhysicsSystem } from './gltf-dynamic-system';
import { GltfSceneSyncSystem } from './gltf-scene-sync';
import { gltfDynamicRecipe, gltfLoadRecipe } from './recipes';
import { GltfXmlLoadSystem } from './systems';

export const GltfXmlPlugin: Plugin = {
  recipes: [gltfLoadRecipe, gltfDynamicRecipe],
  systems: [GltfXmlLoadSystem, GltfDynamicPhysicsSystem, GltfSceneSyncSystem],
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
        colliderShape: 0,
        bodyType: 0,
      },
    },
    enums: {
      gltfPhysicsPending: {
        colliderShape: {
          box: 0,
          sphere: 1,
          capsule: 2,
        },
        bodyType: {
          dynamic: 0,
          fixed: 1,
          'kinematic-position': 2,
          'kinematic-velocity': 3,
        },
      },
    },
  },
};
