import { MAX_ENTITIES } from '../../core/ecs/constants';

/** 0 = aguardando spawn; 1 = instâncias criadas. */
export const SpawnerPending = {
  spawned: new Uint8Array(MAX_ENTITIES),
} as const;

/** Same semantics as {@link SpawnerPending}, for `<entity place="…">` (deterministic terrain placement). */
export const PlacePending = {
  spawned: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Marks entities spawned on terrain; used to re-align Y after heightmap
 * hot-reload (see `TerrainSpawnSystem`'s reload callback) and to apply a
 * deferred AABB lift once the template's GLB bounds finish loading.
 *
 * Y contract (see `terrain-spawned-y.ts`) — **static / place props only**.
 * DynamicSpawner agents (enemies) do not get this component: Rapier is not
 * wired for them (no Rigidbody/CCT); fake TerrainSpawned snaps are not used.
 *
 * - `yOffset` = foot plant (`baseYOffset` + AABB lift). Never slope sink.
 * - `halfWidth` > 0 → upright edge-sink recomputed at current XZ.
 */
export const TerrainSpawned = {
  /** Foot offset above the mesh surface (base-y-offset + AABB lift). */
  yOffset: new Float32Array(MAX_ENTITIES),
  surfaceEpsilon: new Float32Array(MAX_ENTITIES),
  /**
   * Scaled XZ half-extent for edge-sink. 0 disables sink.
   */
  halfWidth: new Float32Array(MAX_ENTITIES),
  /** 1 when the instance leans with terrain (`align-to-terrain`); drives sink residual. */
  alignToTerrain: new Uint8Array(MAX_ENTITIES),
  /**
   * 1 while the entity spawned with `ground-align="aabb"` but its GLB bounds
   * were not yet cached, so the base lift (`-minY * scaleY`) was skipped and
   * only `baseYOffset` was applied. `TerrainSpawnBoundsCatchUpSystem` reapplies
   * the missing lift in Y once the bounds arrive, then clears this flag.
   */
  aabbPending: new Uint8Array(MAX_ENTITIES),
  /** Y scale used at spawn (needed to recompute `lift = -minY * scaleY`). */
  scaleY: new Float32Array(MAX_ENTITIES),
  /**
   * Surface normal Y at the spawn point (1 when not aligning to terrain). The
   * AABB lift is applied along the normal, so the vertical component is
   * `normalY * lift`. Stored so the catch-up can reproduce the same foot.y.
   */
  normalY: new Float32Array(MAX_ENTITIES),
} as const;
