import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';

/**
 * Lowest terrain height on the lake's rim ring (16 probes at `radius`).
 * Using the minimum keeps the water surface below every rim point, so the
 * lake never "leaks" over a low edge.
 */
export function rimHeight(
  sampler: HeightSampler,
  localX: number,
  localZ: number,
  radius: number
): number {
  let min = Infinity;
  for (let i = 0; i < 16; i++) {
    const a = (i / 16) * Math.PI * 2;
    const h = sampleHeightAt(
      sampler,
      localX + Math.cos(a) * radius,
      localZ + Math.sin(a) * radius
    );
    if (h < min) min = h;
  }
  return Number.isFinite(min) ? min : 0;
}

/**
 * Carve a parabolic bowl into the sampler's height data (in place).
 *
 * Profile: `rimY - depth·(1 − t²)` with `t = dist/radius` — depth at the
 * centre, flush with the original terrain at the rim. Heights only ever go
 * *down* (min), so overlapping lakes and pre-existing valleys are safe.
 *
 * Mutating the sampler is the whole point: chunk meshes, per-chunk Rapier
 * heightfields, the BVH and every gameplay height query read from it, so a
 * single carve keeps them all consistent.
 *
 * Returns true when at least one texel changed.
 */
export function carveBowl(
  sampler: HeightSampler,
  localX: number,
  localZ: number,
  radius: number,
  rimY: number,
  depth: number
): boolean {
  const { data, width, height, worldSize, maxHeight } = sampler;
  if (!data || width < 2 || height < 2 || maxHeight <= 0) return false;

  const half = worldSize / 2;
  const stepX = worldSize / (width - 1);
  const stepZ = worldSize / (height - 1);

  const x0 = Math.max(0, Math.floor((localX - radius + half) / stepX));
  const x1 = Math.min(width - 1, Math.ceil((localX + radius + half) / stepX));
  const z0 = Math.max(0, Math.floor((localZ - radius + half) / stepZ));
  const z1 = Math.min(height - 1, Math.ceil((localZ + radius + half) / stepZ));

  let changed = false;
  for (let zi = z0; zi <= z1; zi++) {
    const wz = zi * stepZ - half;
    for (let xi = x0; xi <= x1; xi++) {
      const wx = xi * stepX - half;
      const dx = wx - localX;
      const dz = wz - localZ;
      const t2 = (dx * dx + dz * dz) / (radius * radius);
      if (t2 >= 1) continue;
      const bowlY = rimY - depth * (1 - t2);
      const target = Math.min(1, Math.max(0, bowlY / maxHeight));
      const i = zi * width + xi;
      if (data[i]! > target) {
        data[i] = target;
        changed = true;
      }
    }
  }
  return changed;
}
