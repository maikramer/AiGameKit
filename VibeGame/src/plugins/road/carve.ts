import {
  applyHeightBrush,
  minEffectiveFalloff,
  minEffectiveWidth,
} from '../terrain/height-brush';
import { corridorAabb, nearestOnPolyline } from '../terrain/corridor';
import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';

/**
 * Roadbed preparation — design profile → stamp sampler (shared height-brush).
 *
 * 1. Survey centerline heights.
 * 2. Terrace profile (multi-pass smooth + grade limit).
 * 3. Grade a platform of `width` with shoulder falloff; optional sink so the
 *    cut reads as an embankment from the side.
 *
 * Density + remesh/collider are the caller's job via the shared ground-mutation
 * phases (see `terrain/ground-mutation.ts`). Ribbon samples analytic
 * `sampleHeightAt` after carve — never mesh-catchup.
 */
export interface RoadCorridorOpts {
  /** Polyline `[x0,z0,...]` in field-local coords, already smoothed/resampled. */
  path: number[];
  /** Full roadbed width (m) — full weight to the design profile. */
  width: number;
  /** Shoulder blend back to natural relief (m). Keep short (min necessary). */
  falloff: number;
  /** Longitudinal smooth window (m). Larger → flatter terrace. */
  window: number;
  /**
   * Max |Δh/Δs| on the design profile after the terrace smooth.
   * Caps cut/fill; omit / `0` = no grade clamp.
   */
  maxGrade?: number;
  /**
   * How far the bed sits below the terrace profile (m). Small positive cut so
   * shoulders read as a bank; 0 = flush with profile.
   */
  platformSink?: number;
}

/** Default design grade (~22%). Steeper natural slopes get cut/fill. */
export const DEFAULT_ROAD_MAX_GRADE = 0.22;

/** Default bed sink below terrace (m) — soft bank, not a trench. */
export const DEFAULT_ROAD_PLATFORM_SINK = 0.12;

/** Multi-pass smooth iterations for the terrace profile. */
export const ROAD_PROFILE_SMOOTH_PASSES = 3;

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

/**
 * Terrace design profile: multi-pass longitudinal smooth then optional grade
 * limit. Soft default — one light smooth left dune-scale bumps on the bed.
 */
export function designRoadProfile(
  arcs: number[],
  heights: number[],
  window: number,
  maxGrade: number,
  passes = ROAD_PROFILE_SMOOTH_PASSES
): number[] {
  let h = heights.slice();
  const w = Math.max(window, 0.01);
  for (let p = 0; p < Math.max(1, passes); p++) {
    h = smoothProfile(arcs, h, w);
  }
  return limitProfileGrade(arcs, h, maxGrade);
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

  // Bed width must span the heightmap lattice. Authored ribbon width (~5–9 m)
  // is far below a 2000/64 texel (~32 m); without this the full-weight corridor
  // never hits a texel centre and the terrain looks untouched.
  const halfWidth =
    minEffectiveWidth(sampler, Math.max(opts.width, 0.1), 1.5) / 2;
  const fall = minEffectiveFalloff(sampler, Math.max(opts.falloff, 0.01));
  const reach = halfWidth + fall;

  const { arcs, heights } = stationProfile(sampler, path);
  const maxGrade =
    opts.maxGrade === undefined ? DEFAULT_ROAD_MAX_GRADE : opts.maxGrade;
  const profile = designRoadProfile(arcs, heights, opts.window, maxGrade);
  const sink =
    opts.platformSink === undefined
      ? DEFAULT_ROAD_PLATFORM_SINK
      : Math.max(0, opts.platformSink);

  const aabb = corridorAabb(path, reach);
  if (!aabb) return false;

  return applyHeightBrush(sampler, {
    minX: aabb.minX,
    maxX: aabb.maxX,
    minZ: aabb.minZ,
    maxZ: aabb.maxZ,
    evalAt(wx, wz) {
      const n = nearestOnPolyline(path, wx, wz);
      if (!n || n.dist >= reach) return null;

      const p0 = profile[n.seg]!;
      const p1 = profile[n.seg + 1]!;
      const targetY = p0 + (p1 - p0) * n.t - sink;

      let weight = 1;
      if (n.dist > halfWidth) {
        const t = (n.dist - halfWidth) / fall;
        weight = 1 - t * t * (3 - 2 * t);
      }
      return { targetY, weight };
    },
  });
}
