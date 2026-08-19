import { parseNumberAttr, splitTokens } from '../../core';
import type { Adapter, Plugin, State } from '../../core';
import {
  getInstancedLodUrls,
  GltfAutoInstanceSystem,
  markGltfInstanced,
  setInstancedLodThreshold,
  setInstancedLodUrl,
} from './auto-instance';
import { GltfLod, GltfPending, GltfPhysicsPending } from './components';
import {
  applyPendingLodThresholds,
  getGltfUrl,
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

/**
 * `lod1-url` / `lod2-url` historically only fed the InstancedMesh2 pool.
 * Animated (non-instanced) loaders also declare those attributes — promote
 * them into {@link setGltfLodUrls} + {@link GltfLod} once `url` and at least
 * one higher LOD are known so {@link GltfLodSystem} can switch meshes.
 */
function promoteDiscreteLodUrls(state: State, entity: number): void {
  const url = getGltfUrl(state, entity);
  if (!url) return;
  const [lod1, lod2] = getInstancedLodUrls(state, entity);
  if (!lod1 && !lod2) return;
  const mid = lod1 ?? url;
  const far = lod2 ?? mid;
  setGltfLodUrls(state, entity, [url, mid, far]);
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
}

export const GltfXmlPlugin: Plugin = {
  recipes: [gltfLoadRecipe, gltfDynamicRecipe],
  systems: [
    GltfXmlLoadSystem,
    GltfDynamicPhysicsSystem,
    GltfSceneSyncSystem,
    GltfLodSystem,
    GltfAutoInstanceSystem,
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
          promoteDiscreteLodUrls(state, entity);
        }) as Adapter,
        'model-url': ((entity, value, state) => {
          setGltfUrl(state, entity, value);
          promoteDiscreteLodUrls(state, entity);
        }) as Adapter,
        'lod-urls': ((entity, value, state) => {
          const parts = splitTokens(value);
          if (parts.length !== 3) return;
          const triple = [parts[0], parts[1], parts[2]] as [
            string,
            string,
            string,
          ];
          setGltfLodUrls(state, entity, triple);
          setGltfUrl(state, entity, parts[0]);
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
          const v = parseNumberAttr(value, Number.NaN);
          if (Number.isNaN(v)) return;
          if (state.hasComponent(entity, GltfLod)) {
            GltfLod.thresholdNear[entity] = v;
          } else {
            setPendingLodThresholdNear(state, entity, v);
          }
          // Also seed the instanced pool's lod0→1 threshold (the first entity
          // to spawn a URL wins; the library bakes LOD distances at attach).
          setInstancedLodThreshold(state, entity, 1, v);
        }) as Adapter,
        'lod-threshold-mid': ((entity, value, state) => {
          const v = parseNumberAttr(value, Number.NaN);
          if (Number.isNaN(v)) return;
          if (state.hasComponent(entity, GltfLod)) {
            GltfLod.thresholdMid[entity] = v;
          } else {
            setPendingLodThresholdMid(state, entity, v);
          }
          // Also seed the instanced pool's lod1→2 threshold.
          setInstancedLodThreshold(state, entity, 2, v);
        }) as Adapter,
        // LOD urls: feed InstancedMesh2 pools AND non-instanced GltfLod roots.
        'lod1-url': ((entity, value, state) => {
          const u = String(value).trim();
          if (u) {
            setInstancedLodUrl(state, entity, 1, u);
            promoteDiscreteLodUrls(state, entity);
          }
        }) as Adapter,
        'lod2-url': ((entity, value, state) => {
          const u = String(value).trim();
          if (u) {
            setInstancedLodUrl(state, entity, 2, u);
            promoteDiscreteLodUrls(state, entity);
          }
        }) as Adapter,
        // `<GLTFLoader instanced="true">` — render through the shared
        // InstancedMesh pool for this URL (one draw call per primitive for
        // every entity using the same GLB). See auto-instance.ts.
        instanced: ((entity, value, state) => {
          const v = String(value).trim().toLowerCase();
          if (v === 'true' || v === '1') markGltfInstanced(state, entity);
        }) as Adapter,
      },
    },
    defaults: {
      gltfPending: {
        loaded: 0,
        retries: 0,
      },
      gltfLod: {
        thresholdNear: 45,
        thresholdMid: 110,
        activeLevel: 0,
        settled: 0,
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
