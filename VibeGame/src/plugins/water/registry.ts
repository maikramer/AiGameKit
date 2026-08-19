import type { State } from '../../core';
import { distanceToPath } from './path-utils';
import type { FlatPath } from './path-utils';

/** Cached flat polylines for river bodies — avoids realloc every query. */
const riverFlatCache = new WeakMap<object, FlatPath>();

export function getRiverFlatPath(
  path: ReadonlyArray<readonly [number, number]>
): FlatPath {
  // Use the array object itself as key when possible; callers pass body.path.
  let flat = riverFlatCache.get(path as object);
  if (!flat) {
    flat = [];
    for (const p of path) flat.push(p[0], p[1]);
    riverFlatCache.set(path as object, flat);
  }
  return flat;
}

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
  /**
   * Radius of the full carve footprint (m): carve margin + the organic
   * outline's outward overshoot. Spawn exclusion (`avoid-water`) uses this so
   * props stay off the carved beach slope, not just out of the water. Optional
   * for legacy bodies — consumers fall back to `radius`.
   */
  carveRadius?: number;
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
  /**
   * Full width of the carve footprint (m): waterline + exposed banks + the
   * outer feather band. Spawn exclusion (`avoid-water`) uses this so props
   * stay off the carved banks, not just out of the water. Optional for legacy
   * bodies — consumers fall back to `width`.
   */
  carveWidth?: number;
  /** Highest surface point (the source). Prefer `surfaceY` for local level. */
  waterY: number;
  /**
   * World-space water-surface height per path point. Rivers descend (and
   * plunge at waterfalls), so a single scalar level is wrong away from the
   * source — queries interpolate this instead when present.
   */
  surfaceY?: number[];
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

interface PathBounds {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

/** XZ bounding box of a river polyline — computed once per path array. */
const riverBoundsCache = new WeakMap<object, PathBounds>();

function getRiverBounds(
  path: ReadonlyArray<readonly [number, number]>
): PathBounds {
  let bounds = riverBoundsCache.get(path as object);
  if (!bounds) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (const p of path) {
      if (p[0] < minX) minX = p[0];
      if (p[0] > maxX) maxX = p[0];
      if (p[1] < minZ) minZ = p[1];
      if (p[1] > maxZ) maxZ = p[1];
    }
    bounds = { minX, maxX, minZ, maxZ };
    riverBoundsCache.set(path as object, bounds);
  }
  return bounds;
}

/** True when the world XZ point lies within `reach(body)` of the body centre/axis. */
function withinReach(
  body: WaterBody,
  x: number,
  z: number,
  reach: number
): boolean {
  if (body.kind === 'lake') {
    const dx = x - body.x;
    const dz = z - body.z;
    return dx * dx + dz * dz <= reach * reach;
  }
  // river: distance to the polyline ≤ reach. Walking every segment is O(path)
  // per query and this runs for each wader every fixed step, so reject with the
  // polyline's bounding box first — a river covers a sliver of the map, and
  // almost every caller is nowhere near it.
  const bounds = getRiverBounds(body.path);
  if (
    x < bounds.minX - reach ||
    x > bounds.maxX + reach ||
    z < bounds.minZ - reach ||
    z > bounds.maxZ + reach
  ) {
    return false;
  }
  // Flatten the [x,z] pairs.
  return distanceToPath(getRiverFlatPath(body.path), x, z) <= reach;
}

/** True when the world XZ point lies inside a water surface (disc or channel). */
function containsPoint(body: WaterBody, x: number, z: number): boolean {
  return withinReach(
    body,
    x,
    z,
    body.kind === 'lake' ? body.radius : body.width / 2
  );
}

/** True when the point lies inside the full carve footprint (water + carved
 *  banks/beach). Legacy bodies without carve extents fall back to the water
 *  extents, matching the old behaviour. */
function insideCarve(body: WaterBody, x: number, z: number): boolean {
  return withinReach(
    body,
    x,
    z,
    body.kind === 'lake'
      ? (body.carveRadius ?? body.radius)
      : (body.carveWidth ?? body.width) / 2
  );
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

/**
 * True when the world XZ point lies inside a water body's full carve footprint
 * (water + exposed carved banks/beach). The spawner's `avoid-water` uses this
 * so props don't land on the carved slope with their trunks in the channel.
 */
export function isPointNearWater(state: State, x: number, z: number): boolean {
  for (const b of getWaterBodies(state)) {
    if (insideCarve(b, x, z)) return true;
  }
  return false;
}

/**
 * True on the carved bank/beach ring: inside the carve footprint but outside
 * the wet surface. Backs the spawner's `near-water` flag (river rocks, reeds).
 */
export function isPointOnWaterBank(
  state: State,
  x: number,
  z: number
): boolean {
  for (const b of getWaterBodies(state)) {
    if (insideCarve(b, x, z) && !containsPoint(b, x, z)) return true;
  }
  return false;
}

/**
 * Signed distance (m) from the world XZ point to the nearest waterline: 0 at
 * the water's edge, positive on land, negative over the wet surface. Lakes
 * measure from the waterline disc (`shoreRadius`); rivers from the waterline
 * channel (`shoreWidth / 2`). Returns null when no water bodies exist — band
 * rules treat null as "no constraint".
 */
export function distanceToWaterAt(
  state: State,
  x: number,
  z: number
): number | null {
  const bodies = getWaterBodies(state);
  if (bodies.length === 0) return null;
  let best = Infinity;
  for (const b of bodies) {
    const d =
      b.kind === 'lake'
        ? Math.hypot(x - b.x, z - b.z) - b.shoreRadius
        : distanceToPath(getRiverFlatPath(b.path), x, z) -
          (b.shoreWidth ?? b.width) / 2;
    if (d < best) best = d;
  }
  return best;
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

/** Local water level of a body at the XZ point (rivers descend station by
 *  station; lakes are flat). */
export function bodySurfaceYAt(body: WaterBody, x: number, z: number): number {
  if (body.kind === 'lake' || !body.surfaceY || body.surfaceY.length === 0) {
    return body.waterY;
  }
  // Nearest path point wins — stations are metres apart, well inside the
  // vertical tolerance of splash/drag/spawn queries.
  let best = Infinity;
  let bestY = body.waterY;
  for (let i = 0; i < body.path.length; i++) {
    const p = body.path[i]!;
    const dx = x - p[0];
    const dz = z - p[1];
    const d = dx * dx + dz * dz;
    if (d < best) {
      best = d;
      bestY = body.surfaceY[Math.min(i, body.surfaceY.length - 1)]!;
    }
  }
  return bestY;
}

/** Prefer last hit body — waders stay in the same lake/river most frames. */
const lastWaterHitByState = new WeakMap<State, WaterBody | null>();

/** Water surface height at the point, or null when not over water. */
export function waterLevelAt(
  state: State,
  x: number,
  z: number
): number | null {
  const bodies = getWaterBodies(state);
  const last = lastWaterHitByState.get(state);
  if (last && containsPoint(last, x, z)) {
    return bodySurfaceYAt(last, x, z);
  }
  for (const b of bodies) {
    if (b === last) continue;
    if (containsPoint(b, x, z)) {
      lastWaterHitByState.set(state, b);
      return bodySurfaceYAt(b, x, z);
    }
  }
  lastWaterHitByState.set(state, null);
  return null;
}
