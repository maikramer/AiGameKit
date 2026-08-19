import { defineComponent, U8 } from '../../core/ecs/component-storage';

/**
 * `<Vegetation>` — dense static ground cover (grass clumps, flowers).
 * Placement is driven by a SpawnGroupSpec (same path as StaticSpawner);
 * this component only carries vegetation-specific knobs.
 */
export const Vegetation = defineComponent({
  /** 1 = apply vertex wind sway to spawned instanced materials. */
  wind: U8,
  /** 1 once mesh URLs were registered with the wind URL set. */
  windRegistered: U8,
});
