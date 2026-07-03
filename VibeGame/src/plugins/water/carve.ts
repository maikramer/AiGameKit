import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';

/**
 * Amplitude of the organic shoreline perturbation, as a fraction of the lake
 * radius. 0.28 → the rim breathes ±28% around the mean circle: clearly reads
 * as an irregular natural pond from a distance, not a stamped disc. The water
 * mesh, the sand mask and the carve all use this so their outlines agree.
 */
export const SHORE_SHAPE_AMPLITUDE = 0.28;

/**
 * Deterministic shoreline shape: multiplies the base radius by a low-frequency
 * perturbation keyed off the angle and the lake centre. Three sinusoidal
 * harmonics (2/3/5 cycles) with a per-lake phase give an irregular but stable
 * coast — the same lake always carves the same outline, and overlapping lakes
 * don't shimmer between frames. The water mesh, the sand mask and the carve all
 * call this so their outlines agree to the metre.
 *
 * @param angle  Compass angle around the lake centre (radians).
 * @param seedX  Lake centre X (local or world — must match the caller's space).
 * @param seedZ  Lake centre Z.
 * @returns      Radius scale in [1 − A, 1 + A] (A = SHORE_SHAPE_AMPLITUDE).
 */
export function shapeRadius(
  angle: number,
  seedX: number,
  seedZ: number
): number {
  // Two independent per-lake phases from the centre coordinates.
  const phi1 = (seedX * 12.9898 + seedZ * 78.233) * 0.1;
  const phi2 = (seedX * 4.1764 - seedZ * 29.113) * 0.1;
  const a = SHORE_SHAPE_AMPLITUDE;
  const n =
    Math.sin(angle * 2 + phi1) * 0.6 +
    Math.sin(angle * 3 - phi2) * 0.3 +
    Math.sin(angle * 5 + phi1 * 1.7) * 0.1;
  return 1 + n * a;
}

/**
 * Lowest terrain height on the lake's organic rim ring.
 *
 * Probes 32 points around the perturbed outline (`shapeRadius`) and returns the
 * minimum so the water surface never leaks over a low edge. More probes than the
 * old circular version because the outline is no longer a perfect circle.
 */
export function rimHeight(
  sampler: HeightSampler,
  localX: number,
  localZ: number,
  radius: number
): number {
  let min = Infinity;
  for (let i = 0; i < 32; i++) {
    const a = (i / 32) * Math.PI * 2;
    const r = radius * shapeRadius(a, localX, localZ);
    const h = sampleHeightAt(
      sampler,
      localX + Math.cos(a) * r,
      localZ + Math.sin(a) * r
    );
    if (h < min) min = h;
  }
  return Number.isFinite(min) ? min : 0;
}

/**
 * Bowl profile shape exponent. `(1 − t²)^k` is C0 at the rim for k=1 (a visible
 * crease where the basin meets the surrounding terrain) but C1-smooth for k>1.
 * 1.5 keeps the centre deep while the slope flattens to zero at the rim, so the
 * basin blends into the terrain instead of cutting a ring. Derivative
 * d/dt [−depth·(1−t²)^1.5] = −3·t·depth·√(1−t²) → 0 at both t=0 and t=1.
 */
export const BOWL_PROFILE_EXPONENT = 1.5;

/**
 * Fraction of the bowl radius where the terrain meets the water surface.
 *
 * The bowl floor is `rimY − depth·(1−t²)^1.5` (t = dist/radius); the water sits
 * at `rimY − waterOffset`. The shoreline is where they cross:
 * `depth·(1−t²)^1.5 = waterOffset`  →  `t_shore = √(1 − (waterOffset/depth)^(2/3))`.
 * The water disc and the sand mask both key off this so their edges coincide —
 * no more dry sand under the disc, no more floating water over the beach.
 *
 * Clamps to a safe [0, 0.98]: degenerate when waterOffset ≥ depth (lake floor
 * never rises above the surface → entirely submerged) or waterOffset ≤ 0
 * (surface at the rim → no water to fade).
 */
export function shoreFraction(depth: number, waterOffset: number): number {
  if (depth <= 0 || waterOffset <= 0) return 0;
  if (waterOffset >= depth) return 0.98;
  const ratio = waterOffset / depth;
  const t = Math.sqrt(Math.max(0, 1 - Math.pow(ratio, 2 / 3)));
  return Math.min(0.98, Math.max(0, t));
}

/**
 * Carve a smooth bowl into the sampler's height data (in place).
 *
 * Profile: `rimY − depth·(1 − t²)^1.5` with `t = dist/radius` — depth at the
 * centre, flush with the original terrain at the rim, and C1-smooth at the rim
 * (zero slope) so the basin blends into the terrain instead of cutting a ring.
 * Heights only ever go *down* (min), so overlapping lakes and pre-existing
 * valleys are safe.
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
      const dist = Math.sqrt(dx * dx + dz * dz);
      // Organic outline: the effective radius varies with angle, so the bowl is
      // an irregular pond, not a stamped circle. t = dist / shapeRadius(angle).
      const angle = Math.atan2(dz, dx);
      const effR = radius * shapeRadius(angle, localX, localZ);
      const t2 = (dist * dist) / (effR * effR);
      if (t2 >= 1) continue;
      const bowlY = rimY - depth * Math.pow(1 - t2, BOWL_PROFILE_EXPONENT);
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
