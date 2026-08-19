import type { State } from '../../core';
import { getTerrainContext } from './utils';

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
  /**
   * Road: half-width of the carved shelf (bed + talude), metres. Wider than
   * {@link halfWidth} when `avoid-road` must stay off the asphalt but
   * placement still anchors to the analytic carve on the bank.
   */
  carveHalfWidth?: number;
  /**
   * True when this run is a flying span (viaduct). Not paved ground —
   * {@link isPointOnRoad} ignores it so the valley can keep its forest —
   * but {@link crownHitsFlyingDeck} rejects trees whose crown would pierce
   * the deck.
   */
  flying?: boolean;
  /**
   * Per-vertex deck Y (same space as spawn world Y), one value per path
   * vertex. Required on flying runs.
   */
  pathY?: number[];
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

/** True when (x, z) lies inside any registered pad brush's flat core. */
export function pointInAnyPadCore(state: State, x: number, z: number): boolean {
  for (const brush of getGroundBrushes(state)) {
    if (pointInPadCore(brush, x, z)) return true;
  }
  return false;
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
 * the carriageway and its shoulders. Does **not** include the talude; that
 * is {@link pointInRoadCarve}.
 */
export function pointInRoadCorridor(
  brush: GroundBrush,
  x: number,
  z: number
): boolean {
  if (brush.kind !== 'road' || brush.flying) return false;
  const half = brush.halfWidth ?? 0;
  if (half <= 0 || !brush.path || brush.path.length < 4) return false;
  return distanceToBrushPath(brush.path, x, z) <= half;
}

/**
 * True when (x,z) lies on the carved shelf (bed + falloff walls).
 * Falls back to {@link halfWidth} when `carveHalfWidth` is unset.
 */
export function pointInRoadCarve(
  brush: GroundBrush,
  x: number,
  z: number
): boolean {
  if (brush.kind !== 'road' || brush.flying) return false;
  const half = brush.carveHalfWidth ?? brush.halfWidth ?? 0;
  if (half <= 0 || !brush.path || brush.path.length < 4) return false;
  return distanceToBrushPath(brush.path, x, z) <= half;
}

/** Nearest point on a brush polyline: distance plus segment parameter. */
function nearestOnBrushPath(
  path: number[],
  x: number,
  z: number
): { dist: number; seg: number; t: number } {
  let best = { dist: Infinity, seg: 0, t: 0 };
  let seg = 0;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const dx = path[i + 2]! - ax;
    const dz = path[i + 3]! - az;
    const lenSq = dx * dx + dz * dz;
    let t = lenSq > 0 ? ((x - ax) * dx + (z - az) * dz) / lenSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const d = Math.hypot(x - (ax + t * dx), z - (az + t * dz));
    if (d < best.dist) best = { dist: d, seg, t };
    seg++;
  }
  return best;
}

/**
 * Deck Y of a flying road run at (x,z), or null when the point is off the
 * span. `pathY` is in the same space as spawn `worldY`.
 */
export function flyingDeckYAt(
  brush: GroundBrush,
  x: number,
  z: number
): number | null {
  if (!brush.flying || brush.kind !== 'road') return null;
  const half = brush.halfWidth ?? 0;
  if (half <= 0 || !brush.path || brush.path.length < 4) return null;
  if (!brush.pathY || brush.pathY.length < 2) return null;
  const n = nearestOnBrushPath(brush.path, x, z);
  if (n.dist > half) return null;
  const y0 = brush.pathY[n.seg] ?? brush.pathY[0]!;
  const y1 = brush.pathY[n.seg + 1] ?? y0;
  return y0 + (y1 - y0) * n.t;
}

/**
 * True when a prop whose crown sits at `crownY` would pierce a flying
 * viaduct deck. Valley trees stay; only the ones that grow through the
 * asphalt are rejected.
 */
export function crownHitsFlyingDeck(
  state: State,
  x: number,
  z: number,
  crownY: number,
  margin = 0.5
): boolean {
  for (const brush of getGroundBrushes(state)) {
    const deckY = flyingDeckYAt(brush, x, z);
    if (deckY === null) continue;
    if (crownY > deckY - margin) return true;
  }
  return false;
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

/**
 * Signed distance (m) from the world XZ point to the nearest road carve edge:
 * 0 at the carved shelf boundary (bed + talude), negative over the carve,
 * positive on untouched ground. Flying spans (viaducts) are ignored — the
 * valley under them is normal ground. Brush paths are field-local, so the
 * terrain worldOffset is subtracted first. Returns null when no roads exist.
 */
export function distanceToRoadAt(
  state: State,
  x: number,
  z: number
): number | null {
  const brushes = getGroundBrushes(state);
  let hasRoad = false;
  for (const brush of brushes) {
    if (brush.kind === 'road' && !brush.flying) {
      hasRoad = true;
      break;
    }
  }
  if (!hasRoad) return null;

  let offX = 0;
  let offZ = 0;
  for (const [, data] of getTerrainContext(state)) {
    if (!data.initialized) continue;
    offX = data.worldOffset.x;
    offZ = data.worldOffset.z;
    break;
  }

  let best = Infinity;
  for (const brush of brushes) {
    if (brush.kind !== 'road' || brush.flying) continue;
    if (!brush.path || brush.path.length < 4) continue;
    const half = brush.carveHalfWidth ?? brush.halfWidth ?? 0;
    const d = distanceToBrushPath(brush.path, x - offX, z - offZ) - half;
    if (d < best) best = d;
  }
  return best;
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
