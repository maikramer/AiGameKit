import {
  applyHeightBrush,
  minEffectiveFalloff,
  texelInfluenceReach,
} from './height-brush';
import type { HeightSampler } from './height-sampler';

export interface FlattenRectOpts {
  /** Pad centre in field-local world coords. */
  centerX: number;
  centerZ: number;
  /** Half-extents of the fully-flat core (m). */
  halfX: number;
  halfZ: number;
  /** Target terrain height (m) inside the core. */
  targetY: number;
  /** Blend distance (m) from the core edge back to the original terrain. */
  falloff: number;
  /** Corner rounding radius (m) of the core rectangle. */
  cornerRadius: number;
}

/**
 * Level a rounded-rectangle pad into the sampler's height data (in place).
 *
 * The classic "settlement pad" used to seat man-made structures on rugged
 * terrain: inside the core the terrain is set exactly to `targetY` (raising
 * hollows and shaving bumps — unlike the water carve this writes both
 * directions), and a `smoothstep` ring of width `falloff` blends back into
 * the untouched terrain so the pad reads as a groomed embankment, not a
 * cliff-edged stamp.
 *
 * Distance metric is the signed distance to a rounded rectangle, so square
 * pads keep soft corners. Mutating the sampler keeps every consumer (chunk
 * meshes, physics heightfields, BVH, spawner placement) consistent — same
 * contract as the water carve.
 *
 * Returns true when at least one texel changed.
 */
export function flattenRect(
  sampler: HeightSampler,
  opts: FlattenRectOpts
): boolean {
  const { centerX, centerZ, targetY } = opts;
  const cr = Math.max(0, Math.min(opts.cornerRadius, opts.halfX, opts.halfZ));
  const coreX = Math.max(0.01, opts.halfX - cr);
  const coreZ = Math.max(0.01, opts.halfZ - cr);
  // Falloff clamped à resolução do sampler (ver height-brush): pads com
  // falloff menor que o texel produziam degraus/cantos sem blend.
  const fall = minEffectiveFalloff(sampler, Math.max(0.01, opts.falloff));

  const reachX = opts.halfX + fall;
  const reachZ = opts.halfZ + fall;

  return applyHeightBrush(sampler, {
    minX: centerX - reachX,
    maxX: centerX + reachX,
    minZ: centerZ - reachZ,
    maxZ: centerZ + reachZ,
    evalAt(wx, wz) {
      // Signed distance to the rounded-rect core: ≤ 0 inside, grows outward.
      const dx = Math.max(Math.abs(wx - centerX) - coreX, 0);
      const dz = Math.max(Math.abs(wz - centerZ) - coreZ, 0);
      const d = Math.sqrt(dx * dx + dz * dz) - cr;
      if (d >= fall) return null;
      // Blend weight: 1 in the core, smoothstep down to 0 at the falloff edge.
      let weight = 1;
      if (d > 0) {
        const t = d / fall;
        weight = 1 - t * t * (3 - 2 * t);
      }
      return { targetY, weight };
    },
    // Cell-aware core clamp: on a hillside the first falloff texel holds
    // `natural + (plane − natural)·w`, and its bilinear stencil reaches into
    // the core — a one-texel lip at the pad edge that props and plaza
    // arteries have to climb. Clamp it to the pad plane, lower-only,
    // whenever its stencil touches the core (the rounded-rect SDF is
    // 1-Lipschitz, so subtracting the stencil reach is exact).
    guardAt(wx, wz) {
      const dx = Math.max(Math.abs(wx - centerX) - coreX, 0);
      const dz = Math.max(Math.abs(wz - centerZ) - coreZ, 0);
      const d = Math.sqrt(dx * dx + dz * dz) - cr;
      if (d <= 0 || d >= texelInfluenceReach(sampler)) return null;
      return { targetY, weight: 1 };
    },
  });
}
