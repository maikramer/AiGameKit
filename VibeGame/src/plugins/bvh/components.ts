import { defineComponent, U16, U8 } from '../../core/ecs/component-storage';

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
export const BvhTarget = defineComponent({
  include: U8,
  layer: U16,
  dirty: U8,
});
