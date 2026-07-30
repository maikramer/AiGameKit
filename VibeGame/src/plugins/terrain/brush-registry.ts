import type { State } from '../../core';

/** Kind of height-brush mutation stamped into the terrain sampler. */
export type GroundBrushKind = 'pad' | 'road' | 'lake' | 'river';

/**
 * Footprint of a height-brush mutation (pad flatten, road corridor, lake bowl,
 * river channel). Coordinates are field-local XZ (same space as HeightSampler /
 * `flattenRect` / water carves). Consumers (navmesh adaptive source mesh,
 * diagnostics) read this after carvers latch `applied=1`.
 */
export interface GroundBrush {
  kind: GroundBrushKind;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  /** Pad: resolved flatten height (m, field-local). */
  targetY?: number;
  /** Pad: half-extents of the flat core (m). */
  halfX?: number;
  halfZ?: number;
  /** Pad: corner rounding of the flat core (m). */
  cornerRadius?: number;
  /**
   * River/road: flat polyline `[x0,z0,x1,z1,…]` in field-local coords.
   * Used for ribbon navmesh walls and dense local patches.
   */
  path?: number[];
  /** River/road: half-width of the waterline / corridor (m). */
  halfWidth?: number;
}

const GROUND_BRUSHES = new WeakMap<State, GroundBrush[]>();

export function getGroundBrushes(state: State): GroundBrush[] {
  let list = GROUND_BRUSHES.get(state);
  if (!list) {
    list = [];
    GROUND_BRUSHES.set(state, list);
  }
  return list;
}

export function registerGroundBrush(state: State, brush: GroundBrush): void {
  getGroundBrushes(state).push(brush);
}

export function unregisterGroundBrush(state: State, brush: GroundBrush): void {
  const list = getGroundBrushes(state);
  const i = list.indexOf(brush);
  if (i >= 0) list.splice(i, 1);
}

export function clearGroundBrushes(state: State): void {
  const list = GROUND_BRUSHES.get(state);
  if (list) list.length = 0;
}

/** True when (x,z) lies inside a pad brush's flat rounded-rect core (weight=1). */
export function pointInPadCore(
  brush: GroundBrush,
  x: number,
  z: number
): boolean {
  if (brush.kind !== 'pad') return false;
  const halfX = brush.halfX ?? 0;
  const halfZ = brush.halfZ ?? 0;
  if (halfX <= 0 || halfZ <= 0) return false;
  const cx = (brush.minX + brush.maxX) * 0.5;
  const cz = (brush.minZ + brush.maxZ) * 0.5;
  // Falloff ring is outside the core: AABB registered as core±falloff, so the
  // geometric core is halfX/halfZ about centre (not the full AABB).
  const cr = Math.max(0, Math.min(brush.cornerRadius ?? 0, halfX, halfZ));
  const coreX = Math.max(0.01, halfX - cr);
  const coreZ = Math.max(0.01, halfZ - cr);
  const dx = Math.max(Math.abs(x - cx) - coreX, 0);
  const dz = Math.max(Math.abs(z - cz) - coreZ, 0);
  const d = Math.sqrt(dx * dx + dz * dz) - cr;
  return d <= 0;
}

/** Shortest XZ distance from a point to a road/river polyline brush path. */
function distanceToBrushPath(path: number[], x: number, z: number): number {
  let best = Infinity;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const dx = path[i + 2]! - ax;
    const dz = path[i + 3]! - az;
    const lenSq = dx * dx + dz * dz;
    let t = lenSq > 0 ? ((x - ax) * dx + (z - az) * dz) / lenSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(x - (ax + t * dx), z - (az + t * dz));
    if (d < best) best = d;
  }
  return best;
}

/**
 * True when (x,z) lies on a roadbed corridor (distance to path ≤ halfWidth).
 * Uses the graded bed width registered by `<Road flatten>` — props stay off
 * the carriageway and its shoulders.
 */
export function pointInRoadCorridor(
  brush: GroundBrush,
  x: number,
  z: number
): boolean {
  if (brush.kind !== 'road') return false;
  const half = brush.halfWidth ?? 0;
  if (half <= 0 || !brush.path || brush.path.length < 4) return false;
  return distanceToBrushPath(brush.path, x, z) <= half;
}

/**
 * Paved ground the spawner should skip: flatten-road corridor **or** plaza
 * pad core. Backs `avoid-road` on `<SpawnGroup>` / `<Vegetation>`.
 */
export function isPointOnRoad(state: State, x: number, z: number): boolean {
  for (const brush of getGroundBrushes(state)) {
    if (brush.kind === 'road' && pointInRoadCorridor(brush, x, z)) return true;
    if (brush.kind === 'pad' && pointInPadCore(brush, x, z)) return true;
  }
  return false;
}

/** True when AABB intersects the square bake window |x|,|z| ≤ bounds. */
export function brushIntersectsBounds(
  brush: GroundBrush,
  bounds: number
): boolean {
  return !(
    brush.maxX < -bounds ||
    brush.minX > bounds ||
    brush.maxZ < -bounds ||
    brush.minZ > bounds
  );
}
