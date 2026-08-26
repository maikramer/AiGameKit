import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';
import type { State } from '../../core';

/**
 * Spawn occupancy registry: every placement (spawn-group instances, instanced
 * vegetation, `place="…"` entities with colliders, explicit
 * `<SpawnExclusion>` zones) registers an XZ disc. Spawn-group sampling
 * rejects candidates whose disc overlaps a registered one, so rocks don't
 * spawn inside trees, trees don't spawn inside the hut, etc.
 *
 * `SpawnExclusion` zones are always registered and always checked — even when
 * a spawn group sets `avoid-overlaps=0` (dense vegetation still stays out of
 * the city). `avoid-overlaps` only controls whether the group registers its
 * own instance discs.
 *
 * Order-independent by construction: whoever spawns later avoids whoever
 * registered earlier — both spawn paths register and check.
 */

interface SpawnFootprint {
  x: number;
  z: number;
  radius: number;
}

/**
 * Footprints bucketed by XZ cell.
 *
 * The registry is written and read in the same pass — every instance the
 * spawner plants is tested against everything planted before it — so a flat
 * list makes world generation quadratic in prop count. A grid keeps each test
 * to the handful of discs that could actually touch the candidate.
 */
interface SpawnOccupancy {
  cells: Map<number, SpawnFootprint[]>;
}

/** Cell size in metres. Props are metre-scale; this keeps a query to a 3×3-ish
 *  neighbourhood without making the buckets long. */
const OCCUPANCY_CELL = 4;

const occupancyByState = new WeakMap<State, SpawnOccupancy>();

function getOccupancy(state: State): SpawnOccupancy {
  let grid = occupancyByState.get(state);
  if (!grid) {
    grid = { cells: new Map() };
    occupancyByState.set(state, grid);
  }
  return grid;
}

/** Cell key for a cell coordinate pair. Interleaving keeps the two halves of
 *  the world from colliding on the same key for coordinates up to ±2^15 cells
 *  (±130 km at the cell size above). */
function cellKey(cx: number, cz: number): number {
  return ((cx & 0xffff) << 16) | (cz & 0xffff);
}

export function registerSpawnFootprint(
  state: State,
  x: number,
  z: number,
  radius: number
): void {
  if (!(radius > 0)) return;
  const grid = getOccupancy(state);
  const footprint: SpawnFootprint = { x, z, radius };
  // Registered into every cell the disc touches, so a query that lands in any
  // of them sees it.
  const x0 = Math.floor((x - radius) / OCCUPANCY_CELL);
  const x1 = Math.floor((x + radius) / OCCUPANCY_CELL);
  const z0 = Math.floor((z - radius) / OCCUPANCY_CELL);
  const z1 = Math.floor((z + radius) / OCCUPANCY_CELL);
  for (let cz = z0; cz <= z1; cz++) {
    for (let cx = x0; cx <= x1; cx++) {
      const key = cellKey(cx, cz);
      const bucket = grid.cells.get(key);
      if (bucket) bucket.push(footprint);
      else grid.cells.set(key, [footprint]);
    }
  }
}

/**
 * Breathing room (metres) added between any two footprints so sampled props
 * never spawn flush against each other or against fixed placements (huts,
 * chests). Keeps models from looking "glued" even when collider discs merely
 * touch.
 */
const SPAWN_CLEARANCE = 0.6;

/** True when a disc at (x, z) does not overlap any registered footprint. */
export function isSpawnAreaFree(
  state: State,
  x: number,
  z: number,
  radius: number
): boolean {
  const grid = occupancyByState.get(state);
  if (!grid || grid.cells.size === 0) return true;
  // The search only has to reach out by this disc plus the clearance, however
  // big the registered ones are: if two discs overlap, a point of the smaller
  // one lies inside the other, and both AABBs cover that point's cell — so
  // they always share a bucket. Reaching out by the widest registered radius
  // instead (the obvious version) made a single large exclusion zone turn
  // every query into a 600-cell scan.
  const reach = radius + SPAWN_CLEARANCE;
  const x0 = Math.floor((x - reach) / OCCUPANCY_CELL);
  const x1 = Math.floor((x + reach) / OCCUPANCY_CELL);
  const z0 = Math.floor((z - reach) / OCCUPANCY_CELL);
  const z1 = Math.floor((z + reach) / OCCUPANCY_CELL);
  for (let cz = z0; cz <= z1; cz++) {
    for (let cx = x0; cx <= x1; cx++) {
      const bucket = grid.cells.get(cellKey(cx, cz));
      if (!bucket) continue;
      for (const f of bucket) {
        const dx = f.x - x;
        const dz = f.z - z;
        const minDist = f.radius + radius + SPAWN_CLEARANCE;
        if (dx * dx + dz * dz < minDist * minDist) return false;
      }
    }
  }
  return true;
}

export function clearSpawnOccupancy(state: State): void {
  occupancyByState.delete(state);
}

/**
 * Explicit no-spawn zone: `<SpawnExclusion at="16 8" radius="7">`.
 * Registered into the occupancy registry by TerrainSpawnSystem before any
 * group samples positions.
 */
export const SpawnExclusion = defineComponent({
  x: F32,
  z: F32,
  radius: F32,
  registered: U8,
});
