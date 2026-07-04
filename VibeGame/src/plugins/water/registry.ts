import type { State } from '../../core';
import { distanceToPath } from './path-utils';

/** Lake water body: a disc centred at (x,z). */
export interface LakeWaterBody {
  kind: 'lake';
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

/** River water body: a channel along a polyline of given width. */
export interface RiverWaterBody {
  kind: 'river';
  /** Polyline points `[x,z]` in world coords. */
  path: ReadonlyArray<readonly [number, number]>;
  /** Channel width (m). Points within width/2 of the path are "in water". */
  width: number;
  /**
   * Full width of the waterline (m): where the carved channel floor rises to
   * the water surface (`width · shoreFraction(depth, waterOffset)`). The strip
   * [shoreWidth/2, width/2] either side is exposed carved bank; the terrain
   * sand mask keys off it so the sand edge hugs the water edge. Optional for
   * legacy bodies — consumers fall back to `0.95 · width`.
   */
  shoreWidth?: number;
  waterY: number;
}

/** A registered water surface (spawn avoidance / gameplay queries). */
export type WaterBody = LakeWaterBody | RiverWaterBody;

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

/** True when the world XZ point lies inside a water surface (disc or channel). */
function containsPoint(body: WaterBody, x: number, z: number): boolean {
  if (body.kind === 'lake') {
    const dx = x - body.x;
    const dz = z - body.z;
    return dx * dx + dz * dz <= body.radius * body.radius;
  }
  // river: distance to the polyline ≤ width/2. Flatten the [x,z] pairs.
  const flat: number[] = [];
  for (const p of body.path) {
    flat.push(p[0], p[1]);
  }
  return distanceToPath(flat, x, z) <= body.width / 2;
}

/**
 * True when the world XZ point lies inside a water surface. Backs the spawner's
 * `avoid-water` flag and any gameplay splash/swim checks (lakes AND rivers).
 */
export function isPointInWater(state: State, x: number, z: number): boolean {
  for (const b of getWaterBodies(state)) {
    if (containsPoint(b, x, z)) return true;
  }
  return false;
}

/** The water body whose surface contains the world XZ point, or null. */
export function waterBodyAt(
  state: State,
  x: number,
  z: number
): WaterBody | null {
  for (const b of getWaterBodies(state)) {
    if (containsPoint(b, x, z)) return b;
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
    if (containsPoint(b, x, z)) return b.waterY;
  }
  return null;
}
