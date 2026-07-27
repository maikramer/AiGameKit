import { applyHeightBrush, minEffectiveFalloff } from '../terrain/height-brush';
import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';

/**
 * Roadbed preparation (real-world order):
 * 1. Survey centerline heights.
 * 2. Light longitudinal smooth (kill texel noise) + grade limit (walkable).
 * 3. Grade a platform of `width` with a short shoulder falloff.
 *
 * Mutating the sampler keeps mesh LOD, Rapier heightfields, BVH, spawners and
 * the road ribbon on the **same** prepared surface. The ribbon must run
 * *after* this carve and sample `sampleHeightAt` — never lift above it.
 */
export interface RoadCorridorOpts {
  /** Polyline `[x0,z0,...]` in field-local coords, already smoothed/resampled. */
  path: number[];
  /** Full roadbed width (m) — full weight to the design profile. */
  width: number;
  /** Shoulder blend back to natural relief (m). Keep short (min necessary). */
  falloff: number;
  /** Light moving-average window along arc (m). Not a highway cut. */
  window: number;
  /**
   * Max |Δh/Δs| on the design profile after the light smooth.
   * Caps cut/fill to what walking needs; omit / `0` = no grade clamp.
   */
  maxGrade?: number;
}

/** Default design grade (~22%). Steeper natural slopes get cut/fill. */
export const DEFAULT_ROAD_MAX_GRADE = 0.22;

/** Per-station heights + cumulative arc (raw longitudinal profile). */
function stationProfile(
  sampler: HeightSampler,
  path: number[]
): { arcs: number[]; heights: number[] } {
  const n = path.length / 2;
  const arcs: number[] = [0];
  const heights: number[] = [];
  for (let i = 0; i < n; i++) {
    heights.push(sampleHeightAt(sampler, path[i * 2]!, path[i * 2 + 1]!));
    if (i > 0) {
      arcs.push(
        arcs[i - 1]! +
          Math.hypot(
            path[i * 2]! - path[(i - 1) * 2]!,
            path[i * 2 + 1]! - path[(i - 1) * 2 + 1]!
          )
      );
    }
  }
  return { arcs, heights };
}

/** Triangular moving average of the profile, window in metres of arc. */
export function smoothProfile(
  arcs: number[],
  heights: number[],
  window: number
): number[] {
  const half = Math.max(window, 0.01) / 2;
  const out: number[] = [];
  for (let i = 0; i < heights.length; i++) {
    let acc = 0;
    let wsum = 0;
    for (let j = 0; j < heights.length; j++) {
      const d = Math.abs(arcs[j]! - arcs[i]!);
      if (d > half) continue;
      const w = 1 - d / half;
      acc += heights[j]! * w;
      wsum += w;
    }
    out.push(wsum > 0 ? acc / wsum : heights[i]!);
  }
  return out;
}

/**
 * Two-pass grade clamp: keep natural heights where slope is OK; only pull
 * toward neighbours when |Δh/Δs| exceeds `maxSlope`. Minimum necessary cut/fill.
 */
export function limitProfileGrade(
  arcs: number[],
  heights: number[],
  maxSlope: number
): number[] {
  if (!(maxSlope > 0) || heights.length < 2) return heights.slice();
  const out = heights.slice();
  for (let i = 1; i < out.length; i++) {
    const ds = Math.max(arcs[i]! - arcs[i - 1]!, 1e-6);
    const maxD = maxSlope * ds;
    const lo = out[i - 1]! - maxD;
    const hi = out[i - 1]! + maxD;
    out[i] = Math.min(hi, Math.max(lo, out[i]!));
  }
  for (let i = out.length - 2; i >= 0; i--) {
    const ds = Math.max(arcs[i + 1]! - arcs[i]!, 1e-6);
    const maxD = maxSlope * ds;
    const lo = out[i + 1]! - maxD;
    const hi = out[i + 1]! + maxD;
    out[i] = Math.min(hi, Math.max(lo, out[i]!));
  }
  return out;
}

/** Design profile: light smooth then optional grade limit. */
export function designRoadProfile(
  arcs: number[],
  heights: number[],
  window: number,
  maxGrade: number
): number[] {
  const smoothed = smoothProfile(arcs, heights, window);
  return limitProfileGrade(arcs, smoothed, maxGrade);
}

/**
 * Writes the prepared roadbed into the sampler (in place). Returns true if
 * any texel changed. Cut and fill — same contract as TerrainPad flattenRect.
 */
export function carveRoadCorridor(
  sampler: HeightSampler,
  opts: RoadCorridorOpts
): boolean {
  const path = opts.path;
  if (path.length < 4) return false;

  const halfWidth = Math.max(opts.width, 0.1) / 2;
  // Falloff clamped to sampler resolution — narrower than a texel aliases to
  // zero writes ("carve that never happens").
  const fall = minEffectiveFalloff(sampler, Math.max(opts.falloff, 0.01));
  const reach = halfWidth + fall;

  const { arcs, heights } = stationProfile(sampler, path);
  const maxGrade =
    opts.maxGrade === undefined ? DEFAULT_ROAD_MAX_GRADE : opts.maxGrade;
  const profile = designRoadProfile(arcs, heights, opts.window, maxGrade);

  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < path.length; i += 2) {
    minX = Math.min(minX, path[i]!);
    maxX = Math.max(maxX, path[i]!);
    minZ = Math.min(minZ, path[i + 1]!);
    maxZ = Math.max(maxZ, path[i + 1]!);
  }

  const segCount = path.length / 2 - 1;
  return applyHeightBrush(sampler, {
    minX: minX - reach,
    maxX: maxX + reach,
    minZ: minZ - reach,
    maxZ: maxZ + reach,
    evalAt(wx, wz) {
      let bestD = Infinity;
      let bestSeg = 0;
      let bestT = 0;
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
        }
      }
      if (bestD >= reach) return null;

      const p0 = profile[bestSeg]!;
      const p1 = profile[bestSeg + 1]!;
      const targetY = p0 + (p1 - p0) * bestT;

      // Full weight on the bed; smoothstep only on the short shoulder.
      let weight = 1;
      if (bestD > halfWidth) {
        const t = (bestD - halfWidth) / fall;
        weight = 1 - t * t * (3 - 2 * t);
      }
      return { targetY, weight };
    },
  });
}
