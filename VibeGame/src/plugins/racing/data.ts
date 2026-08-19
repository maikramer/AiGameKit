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
  itemBoxes.length = 0;
  trackObstacles.length = 0;
  ramps.length = 0;
  oilSlicks.length = 0;
  fireballs.length = 0;
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
  /** 1 when the obstacle shatters on the first hit instead of deflecting. */
  breakable: number;
  /** Index into the track-space obstacle list (movement + cooldown owner). */
  trackIdx: number;
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
  bounce = 0.45,
  breakable = 0,
  trackIdx = -1
): number {
  const index = obstacles.length;
  obstacles.push({ x, z, radius, bounce, breakable, trackIdx });
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
 * collision query sees it at the new spot (moving hazards).
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
 * Remove world obstacles by index (generated hazards being re-rolled) and
 * rebuild the spatial hash — a splice would otherwise leave stale buckets.
 */
export function removeTrackObstacles(indices: number[]): void {
  if (indices.length === 0) return;
  const drop = new Set(indices);
  const kept = obstacles.filter((_, i) => !drop.has(i));
  obstacles.length = 0;
  obstacles.push(...kept);
  obstacleGrid.clear();
  for (let i = 0; i < obstacles.length; i++) {
    const key = cellKey(obstacles[i]!.x, obstacles[i]!.z);
    const bucket = obstacleGrid.get(key);
    if (bucket) bucket.push(i);
    else obstacleGrid.set(key, [i]);
  }
}

/** Point a world obstacle at its track-space record (crate cooldown lookups). */
export function setWorldObstacleTrackIdx(
  worldIndex: number,
  trackIdx: number
): void {
  const o = obstacles[worldIndex];
  if (o) o.trackIdx = trackIdx;
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

// ---- Pickup orbs ---------------------------------------------------------------

// ---- Item boxes --------------------------------------------------------------

/**
 * Item box record, stored in track space so the proximity test can run in
 * arc-length terms before resolving to world XYZ for the visual. The contents
 * are rolled on collection, so the box itself carries no kind.
 */
export interface ItemBoxDef {
  /** BitECS entity id (the box visual / ItemBox component slot). */
  eid: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** Respawn-after-collect delay (s). 0 = single-use. */
  respawnAfter: number;
}

const itemBoxes: ItemBoxDef[] = [];

/** Register an item box at the given track position. Returns the slot. */
export function addItemBox(
  s: number,
  lateral: number,
  respawnAfter = 5
): number {
  const index = itemBoxes.length;
  itemBoxes.push({ eid: -1, s, lateral, respawnAfter });
  return index;
}

export function getItemBoxes(): readonly ItemBoxDef[] {
  return itemBoxes;
}

export function clearItemBoxes(): void {
  itemBoxes.length = 0;
}

// ---- Ramps -------------------------------------------------------------------

/**
 * A jump ramp spanning a stretch of the track. Grounded cars inside the span
 * climb the wedge profile; leaving the far end converts speed into `vertical
 * = slope · speed`, reusing the crest-launch machinery in the controller.
 */
export interface TrackRamp {
  /** Arc position where the ramp starts (m). */
  s: number;
  /** Ramp length along the track (m). */
  length: number;
  /** Lateral width the ramp covers (m), centred on `lateral`. */
  width: number;
  /** Lateral centre of the ramp (m). */
  lateral: number;
  /** Wedge height at the far end (m). */
  height: number;
}

const ramps: TrackRamp[] = [];

export function addTrackRamp(
  s: number,
  length: number,
  width: number,
  height: number,
  lateral = 0
): number {
  const index = ramps.length;
  ramps.push({ s, length, width, height, lateral });
  return index;
}

export function getTrackRamps(): readonly TrackRamp[] {
  return ramps;
}

/** The ramp covering an arc position (if any). */
export function rampAt(s: number, lateral: number): TrackRamp | undefined {
  for (const r of ramps) {
    const into = s - r.s;
    if (into < 0 || into > r.length) continue;
    if (Math.abs(lateral - r.lateral) > r.width * 0.5) continue;
    return r;
  }
  return undefined;
}

/**
 * Wedge height (m) under an arc position — a linear ramp profile, so the lip
 * slope (and therefore the launch) is exactly `height / length`. 0 off-ramp.
 */
export function rampHeightAt(s: number, lateral: number): number {
  const r = rampAt(s, lateral);
  if (!r) return 0;
  const t = (s - r.s) / r.length;
  return t * r.height;
}

export function clearTrackRamps(): void {
  ramps.length = 0;
}

// ---- Oil slicks --------------------------------------------------------------

/** A dropped oil patch. The first car to drive over it spins; then it is spent. */
export interface OilSlick {
  /** BitECS entity id (visual slot), -1 until the visual system builds it. */
  eid: number;
  /** Who dropped it (immune for the first moments). */
  ownerId: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** Seconds before the patch evaporates. */
  ttl: number;
}

const oilSlicks: OilSlick[] = [];

export function addOilSlick(
  ownerId: number,
  s: number,
  lateral: number,
  ttl = 12
): number {
  // Cap the live patch count: the oldest patch evaporates first.
  if (oilSlicks.length >= 6) removeOilSlick(0);
  const index = oilSlicks.length;
  oilSlicks.push({ eid: -1, ownerId, s, lateral, ttl });
  return index;
}

export function getOilSlicks(): readonly OilSlick[] {
  return oilSlicks;
}

export function removeOilSlick(index: number): void {
  oilSlicks.splice(index, 1);
}

export function clearOilSlicks(): void {
  oilSlicks.length = 0;
}

// ---- Fireballs ---------------------------------------------------------------

/** A homing fireball travelling along the track toward the car ahead. */
export interface Fireball {
  /** BitECS entity id (visual slot), -1 until the visual system builds it. */
  eid: number;
  /** Who fired it (immune for the first instants). */
  ownerId: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** Travel speed along the track (m/s). */
  speed: number;
  /** Seconds before it fizzles out. */
  ttl: number;
}

const fireballs: Fireball[] = [];

export function addFireball(
  ownerId: number,
  s: number,
  lateral: number,
  speed = 52,
  ttl = 4.5
): number {
  const index = fireballs.length;
  fireballs.push({ eid: -1, ownerId, s, lateral, speed, ttl });
  return index;
}

export function getFireballs(): readonly Fireball[] {
  return fireballs;
}

export function removeFireball(index: number): void {
  fireballs.splice(index, 1);
}

export function clearFireballs(): void {
  fireballs.length = 0;
}

// ---- Track-space obstacles --------------------------------------------------

/**
 * Track-side obstacle record. The collision side-car uses world XZ, so this
 * keeps both the track-space anchor (position, movement) and the index into
 * the world obstacle list (so movement can resync the spatial hash).
 */
export interface TrackSpaceObstacle {
  /** Obstacle visual / TrackObstacleState component slot. */
  eid: number;
  /** Index into the world-space obstacle list (`addTrackObstacle`). */
  worldIndex: number;
  /** Arc position (m). */
  s: number;
  /** Lateral offset from the centerline (m). */
  lateral: number;
  /** Collision radius (m). */
  radius: number;
  /** Speed retained after a hit (0..1). */
  bounce: number;
  /** See ObstacleKind. */
  kind: number;
  /** See ObstacleMoveMode. */
  moveMode: number;
  /** Sweep/travel speed. */
  moveSpeed: number;
  /** Sweep half-amplitude (m). */
  moveRange: number;
  /** Sweep phase offset (rad). */
  movePhase: number;
  /** Rest arc position (m). */
  baseS: number;
  /** Rest lateral offset (m). */
  baseLateral: number;
}

const trackObstacles: TrackSpaceObstacle[] = [];

/** Register a track-space obstacle. Returns the slot index. */
export function addTrackObstacleByS(
  s: number,
  lateral: number,
  radius: number,
  bounce: number,
  kind: number,
  eid = -1,
  worldIndex = -1,
  move: Pick<
    TrackSpaceObstacle,
    'moveMode' | 'moveSpeed' | 'moveRange' | 'movePhase'
  > = {
    moveMode: 0,
    moveSpeed: 0,
    moveRange: 0,
    movePhase: 0,
  }
): number {
  const index = trackObstacles.length;
  trackObstacles.push({
    eid,
    worldIndex,
    s,
    lateral,
    radius,
    bounce,
    kind,
    baseS: s,
    baseLateral: lateral,
    ...move,
  });
  return index;
}

export function getTrackSpaceObstacles(): readonly TrackSpaceObstacle[] {
  return trackObstacles;
}

/** Drop the track-space records of the given entity ids (layout re-roll). */
export function removeTrackSpaceObstacles(eids: Set<number>): void {
  for (let i = trackObstacles.length - 1; i >= 0; i--) {
    const o = trackObstacles[i]!;
    if (o.eid >= 0 && eids.has(o.eid)) trackObstacles.splice(i, 1);
  }
}

export function clearTrackSpaceObstacles(): void {
  trackObstacles.length = 0;
}
