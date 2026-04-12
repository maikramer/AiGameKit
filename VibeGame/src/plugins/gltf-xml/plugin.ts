import type { Adapter, Plugin } from '../../core';
import { GltfLod, GltfPending, GltfPhysicsPending } from './components';
import {
  applyPendingLodThresholds,
  setGltfLodUrls,
  setGltfUrl,
  setPendingLodThresholdMid,
  setPendingLodThresholdNear,
} from './context';
import { GltfDynamicPhysicsSystem } from './gltf-dynamic-system';
import { GltfLodSystem } from './gltf-lod-system';
import { GltfSceneSyncSystem } from './gltf-scene-sync';
import { gltfDynamicRecipe, gltfLoadRecipe } from './recipes';
import { GltfXmlLoadSystem } from './systems';

export const GltfXmlPlugin: Plugin = {
  recipes: [gltfLoadRecipe, gltfDynamicRecipe],
  systems: [
    GltfXmlLoadSystem,
    GltfDynamicPhysicsSystem,
    GltfSceneSyncSystem,
    GltfLodSystem,
  ],
  components: {
    gltfPending: GltfPending,
    gltfPhysicsPending: GltfPhysicsPending,
    gltfLod: GltfLod,
  },
  config: {
    adapters: {
      gltfPending: {
        url: ((entity, value, state) => {
          setGltfUrl(state, entity, value);
        }) as Adapter,
        'lod-urls': ((entity, value, state) => {
          const parts = String(value)
            .trim()
            .split(/\s+/)
            .filter(Boolean);
          if (parts.length !== 3) return;
          const triple = [parts[0], parts[1], parts[2]] as [
            string,
            string,
            string,
          ];
          setGltfLodUrls(state, entity, triple);
          setGltfUrl(state, entity, parts[1]);
          if (!state.hasComponent(entity, GltfLod)) {
            state.addComponent(entity, GltfLod);
          }
          applyPendingLodThresholds(
            state,
            entity,
            (v) => {
              GltfLod.thresholdNear[entity] = v;
            },
            (v) => {
              GltfLod.thresholdMid[entity] = v;
            }
          );
        }) as Adapter,
        'lod-threshold-near': ((entity, value, state) => {
          const v = parseFloat(String(value));
          if (Number.isNaN(v)) return;
          if (state.hasComponent(entity, GltfLod)) {
            GltfLod.thresholdNear[entity] = v;
          } else {
            setPendingLodThresholdNear(state, entity, v);
          }
        }) as Adapter,
        'lod-threshold-mid': ((entity, value, state) => {
          const v = parseFloat(String(value));
          if (Number.isNaN(v)) return;
          if (state.hasComponent(entity, GltfLod)) {
            GltfLod.thresholdMid[entity] = v;
          } else {
            setPendingLodThresholdMid(state, entity, v);
          }
        }) as Adapter,
      },
    },
    defaults: {
      gltfPending: {
        loaded: 0,
      },
      gltfLod: {
        thresholdNear: 40,
        thresholdMid: 120,
        activeLevel: 1,
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
