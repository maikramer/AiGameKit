import type { State } from '../../core';

/** A registered water surface (for spawn avoidance / gameplay queries). */
export interface WaterBody {
  x: number;
  z: number;
  /** Full bowl radius (m). Spawner/navmesh membership + the disc geometry. */
  radius: number;
  /**
   * Radius of the waterline (m): where the carved bowl floor rises to the
   * water surface. Inside it is water; the ring [shoreRadius, radius] is the
   * beach floor. The water disc fades to zero alpha here, and the terrain sand
   * mask keys off it so the two edges coincide.
   */
  shoreRadius: number;
  waterY: number;
}

const WATER_BODIES = new WeakMap<State, WaterBody[]>();

export function getWaterBodies(state: State): WaterBody[] {
  let list = WATER_BODIES.get(state);
  if (!list) {
    list = [];
    WATER_BODIES.set(state, list);
  }
  return list;
}

export function registerWaterBody(state: State, body: WaterBody): void {
  getWaterBodies(state).push(body);
}

export function unregisterWaterBody(state: State, body: WaterBody): void {
  const list = getWaterBodies(state);
  const i = list.indexOf(body);
  if (i >= 0) list.splice(i, 1);
}

/**
 * True when the world XZ point lies inside a water surface. Backs the
 * spawner's `avoid-water` flag (which parsed but checked nothing before
 * lakes existed) and any gameplay splash/swim checks.
 */
export function isPointInWater(state: State, x: number, z: number): boolean {
  for (const b of getWaterBodies(state)) {
    const dx = x - b.x;
    const dz = z - b.z;
    if (dx * dx + dz * dz <= b.radius * b.radius) return true;
  }
  return false;
}

/** The water body whose bowl contains the world XZ point, or null. */
export function waterBodyAt(
  state: State,
  x: number,
  z: number
): WaterBody | null {
  for (const b of getWaterBodies(state)) {
    const dx = x - b.x;
    const dz = z - b.z;
    if (dx * dx + dz * dz <= b.radius * b.radius) return b;
  }
  return null;
}

/** Water surface height at the point, or null when not over water. */
export function waterLevelAt(
  state: State,
  x: number,
  z: number
): number | null {
  for (const b of getWaterBodies(state)) {
    const dx = x - b.x;
    const dz = z - b.z;
    if (dx * dx + dz * dz <= b.radius * b.radius) return b.waterY;
  }
  return null;
}
