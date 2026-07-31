import { corridorAabb, nearestOnPolyline } from '../terrain/corridor';
import { pathLength as corridorPathLength } from '../terrain/corridor';

/** Flat polyline in world XZ: `[x0, z0, x1, z1, ...]`. Must have ≥ 2 points. */
export type FlatPath = number[];

export interface PathAabb {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/** Bounding box of all path points, expanded by `pad` on every side. */
export function pathAabb(path: FlatPath, pad: number): PathAabb {
  if (path.length < 4) {
    throw new Error('pathAabb: path must have at least 2 points (4 numbers)');
  }
  // Shared implementation lives in terrain/corridor (returns null on short
  // paths — callers here guarantee ≥ 2 points already).
  return corridorAabb(path, pad)!;
}

/**
 * Resample the polyline at roughly `spacing`-metre intervals, preserving every
 * original node (corners stay sharp). Consumers that follow terrain (river
 * carve/surface) need dense stations: with authored nodes 20–40 m apart, any
 * per-node terrain sampling turns into long straight ramps that float over
 * dips and tunnel through rises between nodes.
 */
export function resamplePath(path: FlatPath, spacing: number): FlatPath {
  if (path.length < 4) {
    throw new Error(
      'resamplePath: path must have at least 2 points (4 numbers)'
    );
  }
  const step = Math.max(0.5, spacing);
  const out: number[] = [path[0]!, path[1]!];
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const bx = path[i + 2]!;
    const bz = path[i + 3]!;
    const len = Math.hypot(bx - ax, bz - az);
    const steps = Math.max(1, Math.ceil(len / step));
    for (let s = 1; s <= steps; s++) {
      const f = s / steps;
      out.push(ax + (bx - ax) * f, az + (bz - az) * f);
    }
  }
  return out;
}

/** Total length of the polyline (sum of segment lengths). */
export function pathLength(path: FlatPath): number {
  return corridorPathLength(path);
}

/**
 * Shortest distance from point P to segment AB. Falls back to the nearer
 * endpoint distance when P projects outside the segment.
 */
export function distanceToSegment(
  px: number,
  pz: number,
  ax: number,
  az: number,
  bx: number,
  bz: number
): number {
  const dx = bx - ax;
  const dz = bz - az;
  const lenSq = dx * dx + dz * dz;
  if (lenSq === 0) return Math.hypot(px - ax, pz - az);
  let t = ((px - ax) * dx + (pz - az) * dz) / lenSq;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const cx = ax + t * dx;
  const cz = az + t * dz;
  return Math.hypot(px - cx, pz - cz);
}

/** Shortest distance from point P to the polyline (min over all segments). */
export function distanceToPath(path: FlatPath, px: number, pz: number): number {
  if (path.length < 4) {
    throw new Error(
      'distanceToPath: path must have at least 2 points (4 numbers)'
    );
  }
  // Shared nearest-on-polyline lives in terrain/corridor.
  const nearest = nearestOnPolyline(path, px, pz);
  if (!nearest) return Infinity;
  return nearest.dist;
}
