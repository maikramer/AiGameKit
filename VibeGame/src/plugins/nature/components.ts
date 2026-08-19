import { defineComponent, U8 } from '../../core/ecs/component-storage';

/**
 * `<NatureSpawner>` — rule-driven composite scatter. The planner evaluates
 * site features (altitude/slope/biome/water/road/noise) per candidate point
 * and emits one SpawnGroupSpec per species with explicit points; placement
 * itself runs through the shared TerrainSpawnSystem.
 */
export const Nature = defineComponent({
  /** 1 once the planner emitted the per-species SpawnGroupSpecs. */
  planned: U8,
});
