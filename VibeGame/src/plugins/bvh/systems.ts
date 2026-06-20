import type { System } from '../../core';
import { disposeBvhContext } from './utils';
import { syncStaticMeshBvh } from './static-meshes';
import { syncTerrainBvh } from './terrain';

// Terrain BVH is built once per terrain entity. After all terrains are
// registered, the system early-returns immediately, so polling each tick is
// cheap. Static GLTF sync is similar but also handles rigidbody Fixed→Dynamic
// transitions and entity destruction, so it polls each simulation tick.

export const BvhTerrainSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    syncTerrainBvh(state);
  },
};

export const BvhStaticMeshSyncSystem: System = {
  group: 'simulation',
  after: [BvhTerrainSyncSystem],
  update: (state) => {
    syncStaticMeshBvh(state);
  },
  // Sweep the shared bvh context (terrain + static meshes) on State teardown.
  // Only one bvh system owns this so the context is cleared exactly once.
  dispose: (state) => {
    disposeBvhContext(state);
  },
};
