import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * `<Vegetation>` — dense static ground cover (grass clumps, flowers).
 * Placement is driven by a SpawnGroupSpec (same path as StaticSpawner);
 * this component only carries vegetation-specific knobs.
 */
export const Vegetation = {
  /** 1 = apply vertex wind sway to spawned instanced materials. */
  wind: new Uint8Array(MAX_ENTITIES),
  /** 1 once mesh URLs were registered with the wind URL set. */
  windRegistered: new Uint8Array(MAX_ENTITIES),
} as const;
