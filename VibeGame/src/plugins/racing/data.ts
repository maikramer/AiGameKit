import type { State } from '../../core';
import { TrackSpline, type TrackNode, type TrackSplineOptions } from './spline';

/**
 * Sidecar storage for track geometry and track-side obstacles.
 *
 * bitecs components hold numbers only, so the spline (and the obstacle list the
 * vehicle controller resolves against) live here, keyed by the track entity.
 */

const splines = new Map<number, TrackSpline>();

/** Build and store the circuit geometry for a track entity. */
export function setTrackSpline(
  _state: State,
  entity: number,
  nodes: TrackNode[],
  options?: TrackSplineOptions
): TrackSpline {
  const spline = new TrackSpline(nodes, options);
  splines.set(entity, spline);
  return spline;
}

/** Store an already-built spline (used by tests and by generated tracks). */
export function attachTrackSpline(entity: number, spline: TrackSpline): void {
  splines.set(entity, spline);
}

export function getTrackSpline(entity: number): TrackSpline | undefined {
  return splines.get(entity);
}

/** The first (usually only) circuit in the scene. */
export function getPrimaryTrackEntity(): number | undefined {
  for (const key of splines.keys()) return key;
  return undefined;
}

export function getAllTrackEntities(): number[] {
  return [...splines.keys()];
}

export function clearTrackData(): void {
  splines.clear();
  obstacles.length = 0;
  obstacleGrid.clear();
  pickups.length = 0;
  trackObstacles.length = 0;
}

// ---- Track-side obstacles ---------------------------------------------------

/**
 * A solid object cars bounce off (tyre stack, boulder, sign post).
 *
 * The plugin resolves these analytically instead of routing them through
 * Rapier: vehicles are transform-driven, obstacles never move, and a circle
 * test against a bucketed list is both cheaper and impossible to tunnel
 * through at 200 km/h.
 */
export interface TrackObstacle {
  x: number;
  z: number;
  /** Collision radius (m). */
  radius: number;
  /** How much speed survives a hit (0..1). */
  bounce: number;
}

const obstacles: TrackObstacle[] = [];
const obstacleGrid = new Map<number, number[]>();
const OBSTACLE_CELL = 16;

function cellKey(x: number, z: number): number {
  const cx = Math.floor(x / OBSTACLE_CELL);
  const cz = Math.floor(z / OBSTACLE_CELL);
  // Cantor-ish pairing into a single number key (fine for the ranges we use).
  return cx * 73856093 + cz * 19349663;
}

/** Register a solid track-side object. Returns its index. */
export function addTrackObstacle(
  x: number,
  z: number,
  radius: number,
  bounce = 0.45
): number {
  const index = obstacles.length;
  obstacles.push({ x, z, radius, bounce });
  const key = cellKey(x, z);
  const bucket = obstacleGrid.get(key);
  if (bucket) bucket.push(index);
  else obstacleGrid.set(key, [index]);
  return index;
}

export function getTrackObstacles(): readonly TrackObstacle[] {
  return obstacles;
}

/**
 * Move a world-space obstacle and keep the spatial hash in sync so the next
 * collision query sees it at the new spot (sidewinder shove).
 */
export function repositionTrackObstacle(
  index: number,
  x: number,
  z: number
): void {
  const o = obstacles[index];
  if (!o) return;
  const oldKey = cellKey(o.x, o.z);
  const bucket = obstacleGrid.get(oldKey);
  if (bucket) {
    const i = bucket.indexOf(index);
    if (i >= 0) bucket.splice(i, 1);
  }
  o.x = x;
  o.z = z;
  const newKey = cellKey(x, z);
  const next = obstacleGrid.get(newKey);
  if (next) next.push(index);
  else obstacleGrid.set(newKey, [index]);
}

export function clearTrackObstacles(): void {
  obstacles.length = 0;
  obstacleGrid.clear();
}

/**
 * Visit every obstacle whose cell touches (x, z). The callback receives the
 * obstacle; returning `true` stops the iteration.
 */
export function forEachNearbyObstacle(
  x: number,
  z: number,
  fn: (o: TrackObstacle) => boolean | void
): void {
  for (let oz = -1; oz <= 1; oz++) {
    for (let ox = -1; ox <= 1; ox++) {
      const bucket = obstacleGrid.get(
        cellKey(x + ox * OBSTACLE_CELL, z + oz * OBSTACLE_CELL)
      );
      if (!bucket) continue;
      for (let i = 0; i < bucket.length; i++) {
        const o = obstacles[bucket[i]!];
        if (o && fn(o) === true) return;
      }
    }
  }
}

export type { TrackNode, TrackSplineOptions };
export { TrackSpline };

// ---- Pickup orbs -----------------------------------------------------------

/**
 * Pickup orb record, stored in track space so the proximity test can run in
 * arc-length terms before resolving to world XYZ for the visual.
 */
export interface TrackPickup {
  /** BitECS entity id (the orb visual / PickupOrb component slot). */
  eid: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** 0=Pulse, 1=Sidewinder, 2=Shield. */
  kind: number;
  /** Respawn-after-collect delay (s). 0 = single-use. */
  respawnAfter: number;
}

const pickups: TrackPickup[] = [];

/** Register a pickup orb at the given track position. Returns the slot. */
export function addTrackPickup(
  s: number,
  lateral: number,
  kind: number,
  respawnAfter = 6
): number {
  const index = pickups.length;
  pickups.push({ eid: -1, s, lateral, kind, respawnAfter });
  return index;
}

export function getTrackPickups(): readonly TrackPickup[] {
  return pickups;
}

export function clearTrackPickups(): void {
  pickups.length = 0;
}

// ---- Track-space obstacles --------------------------------------------------

/**
 * Track-side obstacle record. The collision side-car uses world XZ, so this
 * returns both the track-space anchor (used by the sidewinder) and the resolved
 * world position (used by the existing obstacle collision).
 */
export interface TrackSpaceObstacle {
  /** Obstacle visual / TrackObstacleState component slot. */
  eid: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** Collision radius (m). */
  radius: number;
  /** Speed retained after a hit (0..1). */
  bounce: number;
  /** 0 barrel, 1 drone, 2 gate. */
  kind: number;
}

const trackObstacles: TrackSpaceObstacle[] = [];

/** Register a track-space obstacle. Returns the slot index. */
export function addTrackObstacleByS(
  s: number,
  lateral: number,
  radius: number,
  bounce: number,
  kind: number,
  eid = -1
): number {
  const index = trackObstacles.length;
  trackObstacles.push({ eid, s, lateral, radius, bounce, kind });
  return index;
}

export function getTrackSpaceObstacles(): readonly TrackSpaceObstacle[] {
  return trackObstacles;
}

export function clearTrackSpaceObstacles(): void {
  trackObstacles.length = 0;
}
