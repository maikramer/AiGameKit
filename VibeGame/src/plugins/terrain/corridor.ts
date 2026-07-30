/**
 * Shared polyline helpers for ground mutation (road / river / channel).
 *
 * Every corridor carver needs the same two primitives: nearest point on a
 * flat XZ polyline, and an AABB expanded by lateral reach. Keeping them here
 * kills the duplicated nearest-seg loops that used to drift (road vs water
 * indexing, missing ±1 texel margins, etc.).
 */

export interface CorridorAabb {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
}

/** Nearest point on a flat polyline `[x0,z0,...]` to `(wx,wz)`. */
export interface NearestOnPolyline {
  /** Lateral distance to the closest point on the polyline (m). */
  dist: number;
  /** Segment index (0 = first segment between nodes 0→1). */
  seg: number;
  /** Parametric t ∈ [0,1] along that segment. */
  t: number;
  /** Closest point X (field-local). */
  cx: number;
  /** Closest point Z (field-local). */
  cz: number;
}

/**
 * Bounding box of all path nodes, expanded by `pad` on every side.
 * Returns null when the path has fewer than 2 points.
 */
export function corridorAabb(path: number[], pad: number): CorridorAabb | null {
  if (path.length < 4) return null;
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < path.length; i += 2) {
    const x = path[i]!;
    const z = path[i + 1]!;
    if (x < minX) minX = x;
    if (z < minZ) minZ = z;
    if (x > maxX) maxX = x;
    if (z > maxZ) maxZ = z;
  }
  return {
    minX: minX - pad,
    maxX: maxX + pad,
    minZ: minZ - pad,
    maxZ: maxZ + pad,
  };
}

/**
 * Nearest point on the polyline. O(segments) — fine for short authored roads;
 * long rivers should stamp per-segment via {@link forEachCorridorSegment}
 * instead of calling this once per texel over a global AABB.
 */
export function nearestOnPolyline(
  path: number[],
  wx: number,
  wz: number
): NearestOnPolyline | null {
  const segCount = path.length / 2 - 1;
  if (segCount < 1) return null;
  let bestD = Infinity;
  let bestSeg = 0;
  let bestT = 0;
  let bestCx = path[0]!;
  let bestCz = path[1]!;
  for (let s = 0; s < segCount; s++) {
    const ax = path[s * 2]!;
    const az = path[s * 2 + 1]!;
    const bx = path[(s + 1) * 2]!;
    const bz = path[(s + 1) * 2 + 1]!;
    const dx = bx - ax;
    const dz = bz - az;
    const lenSq = dx * dx + dz * dz;
    let t = lenSq > 0 ? ((wx - ax) * dx + (wz - az) * dz) / lenSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const cx = ax + t * dx;
    const cz = az + t * dz;
    const d = Math.hypot(wx - cx, wz - cz);
    if (d < bestD) {
      bestD = d;
      bestSeg = s;
      bestT = t;
      bestCx = cx;
      bestCz = cz;
    }
  }
  return { dist: bestD, seg: bestSeg, t: bestT, cx: bestCx, cz: bestCz };
}

/**
 * Visit each segment of a flat polyline. Used by long-corridor stamps (rivers)
 * that cannot afford a global AABB × all-segments nearest search.
 */
export function forEachCorridorSegment(
  path: number[],
  visit: (
    ax: number,
    az: number,
    bx: number,
    bz: number,
    segIndex: number
  ) => void
): void {
  const nodeCount = path.length / 2;
  for (let i = 0; i + 1 < nodeCount; i++) {
    visit(
      path[i * 2]!,
      path[i * 2 + 1]!,
      path[i * 2 + 2]!,
      path[i * 2 + 3]!,
      i
    );
  }
}

/** AABB of one segment expanded by `reach` on every side. */
export function segmentAabb(
  ax: number,
  az: number,
  bx: number,
  bz: number,
  reach: number
): CorridorAabb {
  return {
    minX: Math.min(ax, bx) - reach,
    maxX: Math.max(ax, bx) + reach,
    minZ: Math.min(az, bz) - reach,
    maxZ: Math.max(az, bz) + reach,
  };
}
