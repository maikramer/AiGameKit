import type { State } from '../../core';

/**
 * Sidecar for {@link Track} polyline data. bitecs can't hold arrays, so the
 * centerline lives here keyed by track entity id, mirroring the road plugin's
 * `getRoadData` pattern. The centerline is `[x0,z0,x1,z1,...]` in world XZ.
 */
// (State is referenced by the public helpers below; keep the import.)
export interface TrackData {
  /** Centerline as a flat XZ polyline: [x0,z0,x1,z1,...]. */
  centerline: number[];
  /** Half-width of the drivable corridor (m). Used by HUD/minimap + walls. */
  halfWidth: number;
  /** True if the centerline is a closed circuit (last point connects to first). */
  closed: boolean;
  /**
   * Elevation (Y) per centerline point, same length as `centerline.length / 2`.
   * When present, the track is 3D: the road ribbon follows these heights and the
   * vehicle samples them so it rides up/down hills. Omitted → flat (y=0).
   */
  elevations?: number[];
  /**
   * Theme section per centerline point (e.g. 'city', 'tunnel', 'desert',
   * 'mountain'). Same length as `centerline.length / 2`. Used by the spawner to
   * pick props/palette per section. Omitted → single default theme.
   */
  sections?: string[];
}

const tracks = new Map<number, TrackData>();

export function setTrackData(_state: State, entity: number, data: TrackData): void {
  tracks.set(entity, { ...data });
}

export function getTrackData(_state: State, entity: number): TrackData | undefined {
  return tracks.get(entity);
}

export function getAllTrackEntities(): number[] {
  return [...tracks.keys()];
}

/** Clear all track sidecar data (test isolation). */
export function clearTrackData(): void {
  tracks.clear();
}

// ---- Centerline helpers (pure math, no allocations after warmup) ----

export interface TrackMetrics {
  /** Arc length at each centerline point (point 0 = 0). */
  cumLengths: number[];
  /** Total length of the circuit (m). For closed tracks this includes the wrap. */
  total: number;
}

export function computeTrackMetrics(track: TrackData): TrackMetrics {
  const { centerline, closed } = track;
  const n = Math.floor(centerline.length / 2);
  const cumLengths = new Array<number>(n).fill(0);
  let total = 0;
  for (let i = 1; i < n; i++) {
    const dx = centerline[i * 2]! - centerline[(i - 1) * 2]!;
    const dz = centerline[i * 2 + 1]! - centerline[(i - 1) * 2 + 1]!;
    total += Math.hypot(dx, dz);
    cumLengths[i] = total;
  }
  if (closed && n >= 2) {
    const dx = centerline[0]! - centerline[(n - 1) * 2]!;
    const dz = centerline[1]! - centerline[(n - 1) * 2 + 1]!;
    total += Math.hypot(dx, dz);
  }
  return { cumLengths, total };
}

export interface ProgressSample {
  /** Monotonic distance along the track (m, grows across laps). */
  distance: number;
  /** Fraction of one lap (0..1). */
  fraction: number;
  /** Index of the centerline segment the point is on. */
  segment: number;
}

/**
 * Project a world XZ point onto the track centerline and return its progress.
 * For closed circuits the fraction wraps in [0,1). Uses brute-force nearest-segment
 * (centerlines are short — a few hundred points max — so this is cheap and exact).
 */
export function sampleProgress(
  track: TrackData,
  metrics: TrackMetrics,
  x: number,
  z: number,
  prevSegment = 0
): ProgressSample {
  const { centerline, closed } = track;
  const n = Math.floor(centerline.length / 2);
  if (n < 2) return { distance: 0, fraction: 0, segment: 0 };

  // Search a window around the previous segment first (cheap common case), then
  // fall back to a full scan. The window catches a fast-mover without missing it.
  let bestSeg = prevSegment;
  let bestT = 0;
  let bestDist = Infinity;

  const consider = (i: number): void => {
    const i0 = i % n;
    const i1 = closed ? (i + 1) % n : Math.min(i + 1, n - 1);
    if (i0 === i1) return;
    const ax = centerline[i0 * 2]!;
    const az = centerline[i0 * 2 + 1]!;
    const bx = centerline[i1 * 2]!;
    const bz = centerline[i1 * 2 + 1]!;
    const dx = bx - ax;
    const dz = bz - az;
    const len2 = dx * dx + dz * dz;
    let t = len2 > 0 ? ((x - ax) * dx + (z - az) * dz) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const px = ax + dx * t;
    const pz = az + dz * t;
    const d = (x - px) ** 2 + (z - pz) ** 2;
    if (d < bestDist) {
      bestDist = d;
      bestSeg = i0;
      bestT = t;
    }
  };

  const window = 6;
  for (let k = -window; k <= window; k++) consider(prevSegment + k);
  // Full scan: guarantees we never lose the car on a cut/reset.
  for (let i = 0; i < n - (closed ? 0 : 1); i++) consider(i);

  const segLenBase = bestSeg > 0 ? metrics.cumLengths[bestSeg]! : 0;
  // Distance within this segment.
  const i0 = bestSeg;
  const i1 = closed ? (bestSeg + 1) % n : Math.min(bestSeg + 1, n - 1);
  let segLen = 0;
  if (i0 !== i1) {
    const dx = centerline[i1 * 2]! - centerline[i0 * 2]!;
    const dz = centerline[i1 * 2 + 1]! - centerline[i0 * 2 + 1]!;
    segLen = Math.hypot(dx, dz);
  }
  const lapDistance = segLenBase + bestT * segLen;
  const total = metrics.total > 0 ? metrics.total : 1;
  return {
    distance: lapDistance,
    fraction: closed ? lapDistance / total : Math.min(0.9999, lapDistance / total),
    segment: bestSeg,
  };
}

/**
 * Sample the track elevation at a world XZ point. Projects onto the centerline
 * (nearest segment, same as {@link sampleProgress}) then interpolates the per-point
 * elevation. Returns 0 for flat tracks (no `elevations`).
 */
export function sampleElevation(
  track: TrackData,
  x: number,
  z: number
): number {
  const { centerline, closed, elevations } = track;
  if (!elevations || elevations.length === 0) return 0;
  const n = Math.floor(centerline.length / 2);
  if (n < 2) return 0;

  // Find the nearest segment (brute force; centerlines are short).
  let bestSeg = 0;
  let bestT = 0;
  let bestDist = Infinity;
  const count = closed ? n : n - 1;
  for (let i = 0; i < count; i++) {
    const i1 = (i + 1) % n;
    const ax = centerline[i * 2]!;
    const az = centerline[i * 2 + 1]!;
    const bx = centerline[i1 * 2]!;
    const bz = centerline[i1 * 2 + 1]!;
    const dx = bx - ax;
    const dz = bz - az;
    const len2 = dx * dx + dz * dz;
    let t = len2 > 0 ? ((x - ax) * dx + (z - az) * dz) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const px = ax + dx * t;
    const pz = az + dz * t;
    const d = (x - px) ** 2 + (z - pz) ** 2;
    if (d < bestDist) {
      bestDist = d;
      bestSeg = i;
      bestT = t;
    }
  }

  const i1 = closed ? (bestSeg + 1) % n : Math.min(bestSeg + 1, n - 1);
  const e0 = elevations[bestSeg] ?? 0;
  const e1 = elevations[i1] ?? e0;
  return e0 + (e1 - e0) * bestT;
}

/**
 * Sample the section theme at a world XZ point (nearest centerline point).
 * Returns the default theme if no sections are defined.
 */
export function sampleSection(
  track: TrackData,
  x: number,
  z: number,
  fallback = 'default'
): string {
  const { centerline, sections } = track;
  if (!sections || sections.length === 0) return fallback;
  const n = Math.floor(centerline.length / 2);
  if (n < 1) return fallback;

  let bestSeg = 0;
  let bestDist = Infinity;
  for (let i = 0; i < n; i++) {
    const px = centerline[i * 2]!;
    const pz = centerline[i * 2 + 1]!;
    const d = (x - px) ** 2 + (z - pz) ** 2;
    if (d < bestDist) {
      bestDist = d;
      bestSeg = i;
    }
  }
  return sections[bestSeg] ?? fallback;
}
