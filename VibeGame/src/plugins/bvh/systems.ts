import type { System } from '../../core';
import { syncStaticMeshBvh } from './static-meshes';
import { syncTerrainBvh } from './terrain';

// Terrain BVH is built once per terrain entity. After all terrains are
// registered, the system early-returns immediately, so polling each tick is
// cheap. Static GLTF sync is similar but also handles rigidbody Fixed→Dynamic
// transitions and entity destruction, so it polls each simulation tick.

export const BvhTerrainSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    const { added, total } = syncTerrainBvh(state);
    if (added > 0) {
      console.log(`[bvh] terrain registered: +${added} total=${total}`);
    }
  },
};

export const BvhStaticMeshSyncSystem: System = {
  group: 'simulation',
  after: [BvhTerrainSyncSystem],
  update: (state) => {
    const { added, removed, total } = syncStaticMeshBvh(state);
    if (added > 0 || removed > 0) {
      console.log(`[bvh] static GLTF: +${added} -${removed} total=${total}`);
    }
  },
};
