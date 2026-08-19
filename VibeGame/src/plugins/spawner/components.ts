import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = defineComponent({
  spawned: U8,
});

/** Same semantics as {@link SpawnerPending}, for `<entity place="…">` (deterministic terrain placement). */
export const PlacePending = defineComponent({
  spawned: U8,
});

/**
 * Marks entities spawned on terrain; used to re-align Y after heightmap
 * hot-reload (see `TerrainSpawnSystem`'s reload callback) and to apply a
 * deferred AABB lift once the template's GLB bounds finish loading.
 *
 * Y contract (see `terrain-spawned-y.ts`) — **static / place props only**.
 * `<Creature>` agents skip this component; CCT / heightfield owns runtime Y.
 *
 * - `yOffset` = foot plant (`baseYOffset` + AABB lift).
 */
export const TerrainSpawned = defineComponent({
  /** Foot offset above the mesh surface (base-y-offset + AABB lift). */
  yOffset: F32,
  surfaceEpsilon: F32,
  /** 1 when the instance leans with terrain (`align-to-terrain`). */
  alignToTerrain: U8,
  /**
   * 1 while the entity spawned with `ground-align="aabb"` but its GLB bounds
   * were not yet cached, so the base lift (`-minY * scaleY`) was skipped and
   * only `baseYOffset` was applied. `TerrainSpawnBoundsCatchUpSystem` reapplies
   * the missing lift in Y once the bounds arrive, then clears this flag.
   */
  aabbPending: U8,
  /** Y scale used at spawn (needed to recompute `lift = -minY * scaleY`). */
  scaleY: F32,
  /**
   * Surface normal Y at the spawn point (1 when not aligning to terrain). The
   * AABB lift is applied along the normal, so the vertical component is
   * `normalY * lift`. Stored so the catch-up can reproduce the same foot.y.
   */
  normalY: F32,
});
