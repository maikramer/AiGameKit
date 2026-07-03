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
    minZ: minZ - pad,
    maxX: maxX + pad,
    maxZ: maxZ + pad,
  };
}

/** Total length of the polyline (sum of segment lengths). */
export function pathLength(path: FlatPath): number {
  let total = 0;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const dx = path[i + 2]! - path[i]!;
    const dz = path[i + 3]! - path[i + 1]!;
    total += Math.hypot(dx, dz);
  }
  return total;
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
  let best = Infinity;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const d = distanceToSegment(
      px,
      pz,
      path[i]!,
      path[i + 1]!,
      path[i + 2]!,
      path[i + 3]!
    );
    if (d < best) best = d;
  }
  return best;
}
