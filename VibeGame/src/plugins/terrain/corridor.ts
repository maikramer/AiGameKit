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
  /**
   * Signed lateral offset (m): `+` = right of travel, `-` = left, using the
   * engine's right vector `(tangentZ, -tangentX)` — the same one `TrackSpline`
   * banks around, so a carve tilted by `+bank` raises the same side the track
   * mesh does. Carvers that bank / crown / ditch need the side, not the
   * distance alone.
   */
  signed: number;
  /** Arc position of the closest point along the whole polyline (m). */
  arc: number;
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
  let best: NearestOnPolyline | null = null;
  let arc = 0;
  for (let s = 0; s < segCount; s++) {
    const hit = projectOnSegment(path, s, arc, wx, wz);
    arc += hit.segLen;
    if (!best || hit.near.dist < best.dist) best = hit.near;
  }
  return best;
}

/** Project onto one segment; `segArc` is the arc at its start node. */
function projectOnSegment(
  path: number[],
  seg: number,
  segArc: number,
  wx: number,
  wz: number
): { near: NearestOnPolyline; segLen: number } {
  const ax = path[seg * 2]!;
  const az = path[seg * 2 + 1]!;
  const bx = path[(seg + 1) * 2]!;
  const bz = path[(seg + 1) * 2 + 1]!;
  const dx = bx - ax;
  const dz = bz - az;
  const lenSq = dx * dx + dz * dz;
  const segLen = Math.sqrt(lenSq);
  let t = lenSq > 0 ? ((wx - ax) * dx + (wz - az) * dz) / lenSq : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const cx = ax + t * dx;
  const cz = az + t * dz;
  const dist = Math.hypot(wx - cx, wz - cz);
  // Right of travel for a y-up right-handed frame: right = (dz, -dx).
  const cross = (wx - ax) * dz - (wz - az) * dx;
  return {
    near: {
      dist,
      seg,
      t,
      cx,
      cz,
      signed: cross < 0 ? -dist : dist,
      arc: segArc + segLen * t,
    },
    segLen,
  };
}

/**
 * Cumulative arc length at each node (`arcs[0] = 0`, length = node count).
 * Shared by every carver that interpolates a per-node design list.
 */
export function pathArcs(path: number[]): number[] {
  const n = path.length / 2;
  const arcs: number[] = new Array(Math.max(n, 0));
  if (n <= 0) return [];
  arcs[0] = 0;
  for (let i = 1; i < n; i++) {
    arcs[i] =
      arcs[i - 1]! +
      Math.hypot(
        path[i * 2]! - path[(i - 1) * 2]!,
        path[i * 2 + 1]! - path[(i - 1) * 2 + 1]!
      );
  }
  return arcs;
}

/**
 * Uniform-grid segment index for corridor queries.
 *
 * `nearestOnPolyline` is O(segments) per texel; a race circuit resampled every
 * few metres is hundreds of segments and its AABB covers most of the field, so
 * a naive stamp is O(texels × segments) — seconds of hitching on a lap-sized
 * corridor. The index buckets segment ids into cells of ~`reach`, so a texel
 * only tests the handful of segments that can actually reach it.
 */
export interface CorridorIndex {
  path: number[];
  /** Cumulative arc per node. */
  arcs: number[];
  /** Total polyline length (m). */
  total: number;
  /** Query radius the index was built for (m). */
  reach: number;
  cell: number;
  cols: number;
  rows: number;
  originX: number;
  originZ: number;
  buckets: Int32Array[];
}

/** Minimum cell size so a degenerate reach cannot explode the grid. */
const MIN_INDEX_CELL = 0.5;

/** Cap on grid cells — beyond this the cell grows instead of the memory. */
const MAX_INDEX_CELLS = 1 << 18;

/**
 * Build a {@link CorridorIndex} covering `path` expanded by `reach`.
 * Returns null for degenerate paths (fewer than 2 nodes).
 */
export function createCorridorIndex(
  path: number[],
  reach: number,
  cellSize?: number
): CorridorIndex | null {
  const segCount = path.length / 2 - 1;
  if (segCount < 1) return null;
  const r = Math.max(reach, MIN_INDEX_CELL);
  const aabb = corridorAabb(path, r)!;
  let cell = Math.max(cellSize ?? r, MIN_INDEX_CELL);
  const spanX = aabb.maxX - aabb.minX;
  const spanZ = aabb.maxZ - aabb.minZ;
  // Keep the grid bounded: coarse cells only cost a few extra distance tests.
  while ((spanX / cell + 1) * (spanZ / cell + 1) > MAX_INDEX_CELLS) cell *= 2;
  const cols = Math.max(1, Math.ceil(spanX / cell) + 1);
  const rows = Math.max(1, Math.ceil(spanZ / cell) + 1);

  const lists: number[][] = new Array(cols * rows);
  const arcs = pathArcs(path);
  for (let s = 0; s < segCount; s++) {
    const ax = path[s * 2]!;
    const az = path[s * 2 + 1]!;
    const bx = path[(s + 1) * 2]!;
    const bz = path[(s + 1) * 2 + 1]!;
    const x0 = Math.max(
      0,
      Math.floor((Math.min(ax, bx) - r - aabb.minX) / cell)
    );
    const x1 = Math.min(
      cols - 1,
      Math.floor((Math.max(ax, bx) + r - aabb.minX) / cell)
    );
    const z0 = Math.max(
      0,
      Math.floor((Math.min(az, bz) - r - aabb.minZ) / cell)
    );
    const z1 = Math.min(
      rows - 1,
      Math.floor((Math.max(az, bz) + r - aabb.minZ) / cell)
    );
    for (let zi = z0; zi <= z1; zi++) {
      for (let xi = x0; xi <= x1; xi++) {
        const k = zi * cols + xi;
        (lists[k] ??= []).push(s);
      }
    }
  }

  const buckets: Int32Array[] = new Array(cols * rows);
  for (let i = 0; i < buckets.length; i++) {
    const l = lists[i];
    buckets[i] = l ? Int32Array.from(l) : EMPTY_BUCKET;
  }

  return {
    path,
    arcs,
    total: arcs[arcs.length - 1] ?? 0,
    reach: r,
    cell,
    cols,
    rows,
    originX: aabb.minX,
    originZ: aabb.minZ,
    buckets,
  };
}

const EMPTY_BUCKET = new Int32Array(0);

/** Segments whose cell covers `(wx,wz)`; empty when the point is out of range. */
function bucketAt(index: CorridorIndex, wx: number, wz: number): Int32Array {
  const xi = Math.floor((wx - index.originX) / index.cell);
  const zi = Math.floor((wz - index.originZ) / index.cell);
  if (xi < 0 || zi < 0 || xi >= index.cols || zi >= index.rows) {
    return EMPTY_BUCKET;
  }
  return index.buckets[zi * index.cols + xi]!;
}

/**
 * Nearest point using the index. Returns null when nothing is within the reach
 * the index was built for — callers treat that exactly like `dist >= reach`.
 */
export function nearestOnCorridor(
  index: CorridorIndex,
  wx: number,
  wz: number
): NearestOnPolyline | null {
  const segs = bucketAt(index, wx, wz);
  let best: NearestOnPolyline | null = null;
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]!;
    const hit = projectOnSegment(index.path, s, index.arcs[s]!, wx, wz);
    if (!best || hit.near.dist < best.dist) best = hit.near;
  }
  return best && best.dist <= index.reach ? best : null;
}

/**
 * Every distinct pass of the corridor near `(wx,wz)`, nearest first.
 *
 * A circuit (or a switchback road) can run past the same texel twice at very
 * different heights. Taking the globally nearest station then bulldozes the
 * other arm. Candidates are separated by `arcSeparation` metres of arc so each
 * entry is a genuinely different pass, not two segments of the same corner.
 */
export function nearestCorridorPasses(
  index: CorridorIndex,
  wx: number,
  wz: number,
  arcSeparation: number,
  maxPasses = 4
): NearestOnPolyline[] {
  const segs = bucketAt(index, wx, wz);
  const hits: NearestOnPolyline[] = [];
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i]!;
    const near = projectOnSegment(index.path, s, index.arcs[s]!, wx, wz).near;
    if (near.dist <= index.reach) hits.push(near);
  }
  hits.sort((a, b) => a.dist - b.dist);
  const sep = Math.max(arcSeparation, 0);
  const total = index.total;
  const out: NearestOnPolyline[] = [];
  for (const h of hits) {
    if (out.length >= maxPasses) break;
    let distinct = true;
    for (const k of out) {
      let d = Math.abs(k.arc - h.arc);
      // Closed loops wrap: the seam is not a second pass.
      if (total > 0) d = Math.min(d, total - d);
      if (d <= sep) {
        distinct = false;
        break;
      }
    }
    if (distinct) out.push(h);
  }
  return out;
}

/**
 * Map a per-node value list from `srcPath` onto `dstPath` by **arc fraction**.
 *
 * Carvers rarely stamp the authored polyline: it gets smoothed, resampled and
 * retracted first, so authored per-node widths/heights/banks no longer line up
 * index-for-index. Absolute arc mapping drifts (smoothing shortens the path);
 * fraction mapping keeps the start/end pinned and the middle proportional.
 */
export function resampleNodeValues(
  srcPath: number[],
  values: readonly number[],
  dstPath: number[]
): number[] {
  const srcN = Math.min(srcPath.length / 2, values.length);
  const dstN = dstPath.length / 2;
  if (dstN <= 0) return [];
  if (srcN <= 0) return new Array(dstN).fill(0);
  if (srcN === 1) return new Array(dstN).fill(values[0]!);

  const srcArcs = pathArcs(srcPath.slice(0, srcN * 2));
  const srcTotal = srcArcs[srcN - 1]!;
  const dstArcs = pathArcs(dstPath);
  const dstTotal = dstArcs[dstN - 1]!;
  const out: number[] = new Array(dstN);
  let cursor = 0;
  for (let i = 0; i < dstN; i++) {
    const f = dstTotal > 0 ? dstArcs[i]! / dstTotal : 0;
    const target = f * srcTotal;
    while (cursor < srcN - 2 && srcArcs[cursor + 1]! < target) cursor++;
    const a = srcArcs[cursor]!;
    const b = srcArcs[cursor + 1]!;
    const t = b > a ? Math.min(1, Math.max(0, (target - a) / (b - a))) : 0;
    out[i] = values[cursor]! + (values[cursor + 1]! - values[cursor]!) * t;
  }
  return out;
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

/** Total length of the polyline (sum of segment lengths). */
export function pathLength(path: number[]): number {
  let total = 0;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const dx = path[i + 2]! - path[i]!;
    const dz = path[i + 3]! - path[i + 1]!;
    total += Math.hypot(dx, dz);
  }
  return total;
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
