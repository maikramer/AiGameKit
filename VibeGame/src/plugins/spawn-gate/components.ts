import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Marks an entity to be held in the air at its spawn Y until the terrain it
 * stands on is both (a) heightmap-decoded and (b) backed by a Rapier
 * heightfield collider. Releasing early lets gravity accelerate the body
 * before the one-sided heightfield exists, tunnelling it through the floor.
 */
export const SpawnGateComponent = {
  /** 0 = gated (frozen in air); 1 = latched/released (snapped to ground). */
  ready: new Uint8Array(MAX_ENTITIES),
  /** World Y the entity is held at while the gate is open. */
  yOffset: new Float32Array(MAX_ENTITIES),
  /** Gap kept between the entity origin and the ground surface on snap. */
  skinDistance: new Float32Array(MAX_ENTITIES),
} as const;
