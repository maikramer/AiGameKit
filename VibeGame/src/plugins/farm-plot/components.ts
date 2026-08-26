import {
  defineComponent,
  F32,
  U16,
  U32,
} from '../../core/ecs/component-storage';

/**
 * A tile grid for farming: an N×M field of unit cells anchored in world space.
 *
 * One component per field — the per-tile state lives in side arrays (see
 * `store.ts`), not one entity per tile. A 64×48 field is 3072 tiles; as
 * entities they would add 3072 rows to every TransformHierarchy pass and every
 * `defineQuery` in the app, and buy nothing in return: tiles never move, never
 * collide and are rendered from instanced pools.
 *
 * The grid is a flat plane by design — it belongs on a `<TerrainPad>` plateau
 * (`baseY` matches the pad's stamped height), so no tile ever samples terrain.
 */
export const FarmGrid = defineComponent({
  /** World X of cell (0,0)'s centre — the grid's north-west corner. */
  originX: F32,
  /** World Z of cell (0,0)'s centre. */
  originZ: F32,
  /** Tile edge length (m). */
  cellSize: F32,
  /**
   * World Y the whole grid sits at. `0` = auto: sample the ground at the
   * origin once during setup and pin the grid there.
   */
  baseY: F32,
  /** Soil and crops sit this far above `baseY` so they never z-fight it. */
  surfaceEpsilon: F32,
  cols: U16,
  rows: U16,
  /** Bumped on every mutation — coarse change signal for HUDs and tests. */
  version: U32,
});
