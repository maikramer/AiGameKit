import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Marks an entity whose meshes participate in the static BVH index used by
 * the engine for ground checks, camera occlusion, picking and AI queries.
 *
 * When `include === 1` and the entity has a registered mesh (GLTF root or
 * MeshRenderer instanced slot), the BVH plugin clones its triangle data into
 * a single accelerated mesh registry.
 *
 * Dynamic entities (those whose transform changes every frame) should not be
 * added — Rapier still owns dynamic collision. The BVH is for cheap
 * mesh-vs-ray queries against the world.
 */
export const BvhTarget = {
  include: new Uint8Array(MAX_ENTITIES),
  layer: new Uint16Array(MAX_ENTITIES),
  dirty: new Uint8Array(MAX_ENTITIES),
} as const;
