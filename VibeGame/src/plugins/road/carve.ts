import {
  applyHeightBrush,
  minEffectiveFalloff,
  minEffectiveWidth,
  revertHeightBrush,
  samplerTexelStep,
  texelInfluenceReach,
} from '../terrain/height-brush';
import {
  corridorAabb,
  createCorridorIndex,
  nearestCorridorPasses,
  nearestOnCorridor,
  nearestOnPolyline,
  pathArcs,
} from '../terrain/corridor';
import type { NearestOnPolyline } from '../terrain/corridor';
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
  /**
   * When set, stamp a **constant** field-local Y along the corridor (no
   * surveyed terrace). Used by bridge approaches so both bank lips share one
   * deck plane.
   */
  flatTargetY?: number;
  /**
   * Channel guard: texels already below this field-local Y may only be cut,
   * never filled. Keeps an approach embankment from terracing into water when
   * a coarse texel centre lands in the river.
   */
  noRaiseBelowY?: number;
  /**
   * Lake/river carve discs (field-local). Stamp skips these footprints entirely
   * so a flatten corridor cannot cut a rectangular shelf into a bowl shore.
   */
  preserveDiscs?: ReadonlyArray<{ x: number; z: number; r: number }>;
  /**
   * River carve ribbons (field-local flat path + half-width). Same skip as
   * `preserveDiscs` for channel footprints.
   */
  preserveRibbons?: ReadonlyArray<{ path: number[]; half: number }>;
  /**
   * Skip stamp at field-local (x, z). Used to leave `<TerrainPad>` cores
   * untouched: overlapping plaza arteries each apply `platformSink` (0.12 m)
   * and would otherwise trench the settlement floor under the CCT while
   * props stay on the frozen pad plane.
   */
  skipAt?: (wx: number, wz: number) => boolean;

  // ── Racing / authored-corridor extensions ────────────────────────────────

  /**
   * Per-node bed width (m), one value per `path` node. Overrides `width`.
   * A circuit is not a constant-width ribbon — the straight is wide, the esses
   * pinch — and carving the widest value everywhere flattens half the infield.
   * Use `resampleNodeValues` to map authored widths onto the stamped path.
   */
  widths?: readonly number[];

  /**
   * Authored design elevation per `path` node (field-local m). When present the
   * survey + terrace is skipped entirely: the bed goes exactly where the author
   * put it. This is what a race circuit needs (the driving surface is authored
   * in 3D, the terrain must follow it — not the other way round) and it is also
   * what makes a re-carve idempotent, since nothing is read back from terrain.
   */
  profileY?: readonly number[];

  /**
   * Treat the path as a closed loop (last node coincides with the first).
   * Profile smoothing and the grade clamp then wrap across the seam — without
   * it a circuit gets a step exactly at the start/finish line, where the
   * one-sided moving average runs out of neighbours.
   */
  closed?: boolean;

  /**
   * Cross-slope per `path` node (radians, `+` **raises the right side**, the
   * same sign as `TrackSpline`'s auto-banking). The bed tilts with
   * the track, so a banked corner sits in a tilted shelf instead of a flat one
   * that pokes through the high side and leaves air under the low side. The
   * tilt stops at the bed edge — {@link shoulderWidth} stays flat at whatever
   * height its side of the bed reached.
   */
  banks?: readonly number[];
  /** Hard clamp on |bank| (rad). Default {@link MAX_CORRIDOR_BANK}. */
  maxBank?: number;

  /**
   * Flat run-off apron each side of the bed (m), carved at bed level with full
   * weight before the shoulder falloff starts. Racing needs somewhere to put a
   * car that misses the corner; without it the falloff starts at the white line
   * and every off is a wall.
   */
  shoulderWidth?: number;

  /**
   * Raised lip (m) at the outer edge of the run-off — the sand/gravel bank that
   * catches a car. Negative = drainage ditch instead of a berm.
   */
  bermHeight?: number;
  /** Lateral band the berm rises over (m). Default {@link DEFAULT_BERM_WIDTH}. */
  bermWidth?: number;

  /**
   * How to pick the station when the corridor passes the same texel twice
   * (circuit arms running side by side, switchbacks, overpasses).
   *
   * - `nearest` (default): globally closest station wins — right for a single
   *   pass, wrong under an overpass where the upper arm bulldozes the lower.
   * - `closest-elevation`: among the distinct passes in range, take the one
   *   whose design height is nearest the ground that is actually there. Each
   *   arm then grades its own bed and neither erases the other.
   */
  overlapMode?: 'nearest' | 'closest-elevation';
  /**
   * Arc separation (m) that makes two stations count as different passes.
   * Default {@link DEFAULT_PASS_SEPARATION}.
   */
  passSeparation?: number;

  /**
   * Viaduct threshold (m). Stations whose design bed sits more than this above
   * the **natural** ground are not carved at all: the road is flying, not
   * cutting, so the valley, the forest and the buildings under the span stay
   * exactly as they were. Transitions ease over {@link viaductRamp} so the
   * approach still gets its embankment.
   *
   * Needs a design elevation to compare against — either {@link profileY} or
   * {@link flatTargetY}. With a surveyed terrace the bed follows the ground by
   * construction and nothing is ever "flying".
   *
   * Pair it with the owner journal: the comparison must be against untouched
   * terrain, and the journal is what puts the previous stamp back first.
   */
  viaductClearance?: number;
  /**
   * Arc length (m) over which the carve fades out at each end of a viaduct.
   * Default {@link DEFAULT_VIADUCT_RAMP}.
   */
  viaductRamp?: number;
  /**
   * Receives the per-station carve weight (1 grounded → 0 flying), aligned to
   * {@link path}. Callers feed it to {@link groundedPathRuns} so the
   * `avoid-road` brush stops at the abutments. Density boost follows the
   * full corridor (including the flying span) so the mesh lattice under the
   * deck does not interpolate the valley into the asphalt.
   */
  onGroundMask?: (mask: number[]) => void;

  /**
   * Stable carver id (e.g. `road:12`). Enables the height-brush undo journal:
   * the previous stamp is reverted before this one is written, so regrading a
   * road any number of times lands on the same terrain instead of sinking it
   * one `platformSink` deeper each pass.
   */
  owner?: string;
  /**
   * Add to the owner's journal instead of reverting it first. Used when one
   * carve is made of several stamps (bridge approach stubs): only the first
   * may undo the previous pass, or stub 2 would erase stub 1.
   */
  appendToOwner?: boolean;

  /**
   * Max |Δh/Δs| of the cut wall (m per m). Deep cuts widen the falloff beyond
   * the authored minimum so the wall reads as a natural hillslope instead of a
   * fixed-width trench: `fallEff = max(falloff, easePeak·depth/maxCutSlope)`.
   * Default {@link DEFAULT_CORRIDOR_MAX_CUT_SLOPE}; `0` keeps the authored
   * falloff exact (bridge approaches must not widen into the channel).
   */
  maxCutSlope?: number;

  /**
   * Receives the corridor's effective max lateral reach (solid edge + widest
   * adaptive falloff) so density stamps and ground brushes cover the widened
   * walls.
   */
  onEffectiveReach?: (reach: number) => void;
}

/** Cross-slope clamp (~34°) — past this a "bank" is a cliff. */
export const MAX_CORRIDOR_BANK = 0.6;

/** Default lateral band a berm rises over (m). */
export const DEFAULT_BERM_WIDTH = 2.5;

/** Two stations this far apart in arc (m) are treated as different passes. */
export const DEFAULT_PASS_SEPARATION = 40;

/** Arc length (m) the carve fades over at each end of a viaduct. */
export const DEFAULT_VIADUCT_RAMP = 24;

/**
 * Per-station carve weight for a corridor that may leave the ground.
 *
 * `1` = grounded (carve normally), `0` = flying (do not touch the terrain),
 * with a linear ramp of `ramp` metres of arc between the two so an approach
 * embankment still tapers into the span instead of ending in a wall.
 */
export function viaductMask(
  arcs: readonly number[],
  design: readonly number[],
  natural: readonly number[],
  clearance: number,
  ramp = DEFAULT_VIADUCT_RAMP
): number[] {
  const at = viaductMaskFn(arcs, design, natural, clearance, ramp);
  return arcs.map((a) => at(a));
}

/**
 * Same rule as {@link viaductMask}, evaluated at any arc position.
 *
 * The carve needs this rather than the per-station array: interpolating the
 * station values would make the ramp as long as the station spacing (150 m of
 * gentle fill into a valley on a coarse path), when the whole point is that the
 * transition is `ramp` metres wide no matter how the author spaced the nodes.
 */
export function viaductMaskFn(
  arcs: readonly number[],
  design: readonly number[],
  natural: readonly number[],
  clearance: number,
  ramp = DEFAULT_VIADUCT_RAMP
): (arc: number) => number {
  const n = Math.min(arcs.length, design.length, natural.length);
  const r = Math.max(ramp, 1e-3);
  // Merge consecutive grounded stations into arc intervals.
  const intervals: [number, number][] = [];
  let open: [number, number] | null = null;
  for (let i = 0; i < n; i++) {
    if (design[i]! - natural[i]! <= clearance) {
      if (open) open[1] = arcs[i]!;
      else open = [arcs[i]!, arcs[i]!];
    } else if (open) {
      intervals.push(open);
      open = null;
    }
  }
  if (open) intervals.push(open);
  if (intervals.length === 0) return () => 0;

  return (arc: number) => {
    let best = Infinity;
    for (const [a0, a1] of intervals) {
      if (arc >= a0 && arc <= a1) return 1;
      const d = arc < a0 ? a0 - arc : arc - a1;
      if (d < best) best = d;
    }
    return best >= r ? 0 : 1 - best / r;
  };
}

/**
 * Split a path into the runs where `mask` is above `threshold`.
 *
 * The `avoid-road` ground brush must follow the **grounded** corridor only:
 * stamping it along a viaduct would tell the spawner to keep trees out of the
 * valley the span was built to fly over. Density boost is a separate pass
 * along the full corridor so the mesh under the deck stays fine enough not
 * to slice the asphalt.
 */
export function groundedPathRuns(
  path: number[],
  mask: readonly number[],
  threshold = 0.5
): number[][] {
  const n = Math.min(path.length / 2, mask.length);
  const runs: number[][] = [];
  let current: number[] | null = null;
  for (let i = 0; i < n; i++) {
    if (mask[i]! > threshold) {
      (current ??= []).push(path[i * 2]!, path[i * 2 + 1]!);
    } else if (current) {
      if (current.length >= 4) runs.push(current);
      current = null;
    }
  }
  if (current && current.length >= 4) runs.push(current);
  return runs;
}

export interface FlyingPathRun {
  path: number[];
  /** Per-vertex field-local (or world) Y, same count as `path.length / 2`. */
  pathY?: number[];
}

/**
 * Inverse of {@link groundedPathRuns}: the flying spans. Optional `heights`
 * (one value per path vertex) is sliced in lockstep so a deck Y can ride
 * along the run.
 */
export function flyingPathRuns(
  path: number[],
  mask: readonly number[],
  heights?: number[],
  threshold = 0.5
): FlyingPathRun[] {
  const n = Math.min(path.length / 2, mask.length);
  const runs: FlyingPathRun[] = [];
  let current: number[] | null = null;
  let currentY: number[] | null = null;
  for (let i = 0; i < n; i++) {
    if (mask[i]! <= threshold) {
      (current ??= []).push(path[i * 2]!, path[i * 2 + 1]!);
      if (heights && heights.length > i) {
        (currentY ??= []).push(heights[i]!);
      }
    } else if (current) {
      if (current.length >= 4) {
        runs.push({ path: current, pathY: currentY ?? undefined });
      }
      current = null;
      currentY = null;
    }
  }
  if (current && current.length >= 4) {
    runs.push({ path: current, pathY: currentY ?? undefined });
  }
  return runs;
}

/** Below this many segments an index costs more than the brute-force scan. */
const INDEX_MIN_SEGMENTS = 24;

/** Default design grade (~22%). Steeper natural slopes get cut/fill. */
export const DEFAULT_ROAD_MAX_GRADE = 0.22;

/**
 * Default cut-wall slope cap for the adaptive falloff (~45°, m per m). A deep
 * mountain cut over a fixed-width falloff reads as an artificial trench; the
 * adaptive shoulder widens with the cut depth so the wall keeps a natural
 * hillslope angle. `flatten-max-cut-slope="0"` restores the fixed falloff.
 */
export const DEFAULT_CORRIDOR_MAX_CUT_SLOPE = 1.0;

/** Peak |dy/dt| of the quintic falloff ease (1.875 at t = 0.5). */
const EASE_PEAK_SLOPE = 1.875;

/**
 * Quintic smootherstep — curvature-continuous (C2) at both ends, the 1D
 * Bézier equivalent for the shoulder profile: tangent to the flat run-off at
 * the berm edge and tangent to the natural relief at the outer edge.
 */
const ease5 = (t: number): number => t * t * t * (t * (t * 6 - 15) + 10);

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

/**
 * Triangular moving average of the profile, window in metres of arc.
 *
 * `closed` wraps the window across the seam (last node = first node). Without
 * it the average near the seam is one-sided and a lap-length corridor gets a
 * visible step exactly where the start/finish line is.
 */
export function smoothProfile(
  arcs: number[],
  heights: number[],
  window: number,
  closed = false
): number[] {
  const half = Math.max(window, 0.01) / 2;
  const n = heights.length;
  const total = closed && n > 1 ? arcs[n - 1]! : 0;
  // On a loop the last node repeats the first: averaging both would count the
  // seam station twice and defeat the wrap it is there to enable.
  const m = total > 0 ? n - 1 : n;
  const out: number[] = [];
  for (let i = 0; i < m; i++) {
    let acc = 0;
    let wsum = 0;
    for (let j = 0; j < m; j++) {
      let d = Math.abs(arcs[j]! - arcs[i]!);
      if (total > 0) d = Math.min(d, total - d);
      if (d > half) continue;
      const w = 1 - d / half;
      acc += heights[j]! * w;
      wsum += w;
    }
    out.push(wsum > 0 ? acc / wsum : heights[i]!);
  }
  if (total > 0) out.push(out[0]!);
  return out;
}

/**
 * Two-pass grade clamp: keep natural heights where slope is OK; only pull
 * toward neighbours when |Δh/Δs| exceeds `maxSlope`. Minimum necessary cut/fill.
 */
export function limitProfileGrade(
  arcs: number[],
  heights: number[],
  maxSlope: number,
  closed = false
): number[] {
  if (!(maxSlope > 0) || heights.length < 2) return heights.slice();
  if (closed) return limitProfileGradeClosed(arcs, heights, maxSlope);
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
 * Grade clamp around a loop: the constraint has to travel through the seam in
 * both directions, so each pass walks two laps of the distinct stations (the
 * duplicated end node is not a station of its own).
 */
function limitProfileGradeClosed(
  arcs: number[],
  heights: number[],
  maxSlope: number
): number[] {
  const n = heights.length;
  const m = n - 1;
  if (m < 2) return heights.slice();
  const total = arcs[n - 1]!;
  const out = heights.slice();
  const wrap = (i: number) => ((i % m) + m) % m;
  const arcAt = (i: number) => arcs[wrap(i)]! + Math.floor(i / m) * total;
  const clampStep = (from: number, to: number) => {
    const ds = Math.max(Math.abs(arcAt(to) - arcAt(from)), 1e-6);
    const maxD = maxSlope * ds;
    const anchor = out[wrap(from)]!;
    const cur = out[wrap(to)]!;
    out[wrap(to)] = Math.min(anchor + maxD, Math.max(anchor - maxD, cur));
  };
  for (let i = 1; i < m * 2; i++) clampStep(i - 1, i);
  for (let i = m * 2 - 2; i >= 0; i--) clampStep(i + 1, i);
  out[n - 1] = out[0]!;
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
  passes = ROAD_PROFILE_SMOOTH_PASSES,
  closed = false
): number[] {
  let h = heights.slice();
  const w = Math.max(window, 0.01);
  for (let p = 0; p < Math.max(1, passes); p++) {
    h = smoothProfile(arcs, h, w, closed);
  }
  return limitProfileGrade(arcs, h, maxGrade, closed);
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

  // Re-carve is a rewrite, not a second cut: put back what this owner wrote
  // last time before surveying, or a terraced bed sinks on every regrade.
  if (opts.owner && !opts.appendToOwner) revertHeightBrush(sampler, opts.owner);

  const nodeCount = path.length / 2;
  // Bed width must span the heightmap lattice. Authored ribbon width (~5–9 m)
  // is far below a 2000/64 texel (~32 m); without this the full-weight corridor
  // never hits a texel centre and the terrain looks untouched.
  const halfWidth =
    minEffectiveWidth(sampler, Math.max(opts.width, 0.1), 1.5) / 2;
  const halfAt =
    opts.widths && opts.widths.length >= nodeCount
      ? Array.from({ length: nodeCount }, (_, i) =>
          Math.max(
            minEffectiveWidth(sampler, Math.max(opts.widths![i]!, 0.1), 1.5) /
              2,
            0.05
          )
        )
      : null;
  const maxHalf = halfAt ? Math.max(...halfAt) : halfWidth;

  const fall = minEffectiveFalloff(sampler, Math.max(opts.falloff, 0.01));
  const shoulder = Math.max(0, opts.shoulderWidth ?? 0);
  const bermH = opts.bermHeight ?? 0;
  const bermBand =
    bermH !== 0 ? Math.max(0, opts.bermWidth ?? DEFAULT_BERM_WIDTH) : 0;
  // Everything up to `solidReach` is stamped at full weight; the falloff blends
  // from there back to natural relief.
  const maxSolid = maxHalf + shoulder + bermBand;

  const arcs = pathArcs(path);
  const closed = opts.closed === true && nodeCount > 2;
  const maxGrade =
    opts.maxGrade === undefined ? DEFAULT_ROAD_MAX_GRADE : opts.maxGrade;
  const flatY = opts.flatTargetY;
  const useFlat = flatY !== undefined && Number.isFinite(flatY);
  const authored =
    opts.profileY && opts.profileY.length >= nodeCount ? opts.profileY : null;

  // Natural heights per station, sampled once after the journal revert and
  // before any stamp (shared by the survey, the viaduct mask and the adaptive
  // cut-wall falloff).
  let naturalHeights: number[] | null = null;
  const naturalAt = (): number[] =>
    (naturalHeights ??= stationProfile(sampler, path).heights);

  let profile: readonly number[];
  if (useFlat) {
    profile = arcs.map(() => flatY!);
  } else if (authored) {
    // Authored design elevation wins outright: no survey, no terrace, so the
    // stamp is a pure function of the input and repeats exactly. Trim to the
    // node count: per-node lists indexed on the flat path (one value per
    // coordinate instead of per node) would desync every per-station array.
    profile =
      authored.length === nodeCount ? authored : authored.slice(0, nodeCount);
  } else {
    profile = designRoadProfile(
      arcs,
      naturalAt(),
      opts.window,
      maxGrade,
      ROAD_PROFILE_SMOOTH_PASSES,
      closed
    );
  }
  const sink =
    useFlat || authored
      ? 0
      : opts.platformSink === undefined
        ? DEFAULT_ROAD_PLATFORM_SINK
        : Math.max(0, opts.platformSink);

  // Adaptive cut-wall falloff: the authored `falloff` is the minimum shoulder;
  // a deep cut widens the blend so the wall slope stays under `maxCutSlope`
  // (a natural hillslope instead of a fixed-width trench).
  const cutSlope =
    opts.maxCutSlope === undefined
      ? DEFAULT_CORRIDOR_MAX_CUT_SLOPE
      : Math.max(0, opts.maxCutSlope);
  const fallAt =
    cutSlope > 0
      ? profile.map((y, i) => {
          const depth = Math.max(0, naturalAt()[i]! - (y - sink) - bermH);
          return Math.max(fall, (EASE_PEAK_SLOPE * depth) / cutSlope);
        })
      : null;
  const maxFall = fallAt ? Math.max(...fallAt) : fall;
  const reach = maxSolid + maxFall;
  opts.onEffectiveReach?.(reach);

  const bankLimit = Math.min(
    Math.abs(opts.maxBank ?? MAX_CORRIDOR_BANK),
    MAX_CORRIDOR_BANK
  );
  const banks =
    opts.banks && opts.banks.length >= nodeCount && bankLimit > 0
      ? opts.banks
      : null;

  // Viaduct: where the design bed flies above the natural ground, carve nothing
  // and tell the caller which runs are still on the ground. Sampled here, after
  // the journal revert, so "natural" really means natural.
  const clearance = opts.viaductClearance;
  let maskAt: ((arc: number) => number) | null = null;
  if (clearance !== undefined && clearance > 0 && (authored || useFlat)) {
    const natural = naturalAt();
    const design = profile.map((y) => y - sink);
    maskAt = viaductMaskFn(arcs, design, natural, clearance, opts.viaductRamp);
  }
  opts.onGroundMask?.(
    maskAt ? arcs.map((a) => maskAt!(a)) : new Array(nodeCount).fill(1)
  );

  const aabb = corridorAabb(path, reach);
  if (!aabb) return false;
  const guardY = opts.noRaiseBelowY;
  const discs = opts.preserveDiscs;
  const ribbons = opts.preserveRibbons;

  // Long corridors (a lap is hundreds of segments over most of the field) pay
  // O(texels × segments) without an index; short authored roads do not need one.
  const index =
    nodeCount - 1 >= INDEX_MIN_SEGMENTS
      ? createCorridorIndex(path, reach)
      : null;
  const overlap = opts.overlapMode ?? 'nearest';
  const passSep = Math.max(0, opts.passSeparation ?? DEFAULT_PASS_SEPARATION);

  const lerpNode = (values: readonly number[], n: NearestOnPolyline) => {
    const a = values[n.seg]!;
    const b = values[n.seg + 1] ?? a;
    return a + (b - a) * n.t;
  };
  /** Design bed height at a station, before the lateral profile. */
  const bedYAt = (n: NearestOnPolyline) => lerpNode(profile, n) - sink;

  /** Bed half-width at a station (authored `widths` lerped, else constant). */
  const stationHalf = (n: NearestOnPolyline): number =>
    halfAt ? lerpNode(halfAt, n) : halfWidth;

  /** Full-weight lateral edge at a station: bed + run-off + berm bands. */
  const solidEdgeAt = (n: NearestOnPolyline): number =>
    stationHalf(n) + shoulder + bermBand;

  /**
   * Design surface at `dist` metres off the axis of station `n`: bed plane,
   * bank tilt (each edge holds the height its side of the bed reached) and
   * the berm rise. The falloff blend lives in the weight, never in the
   * target — the same function serves the primary stamp at the texel centre
   * and the cell guard one half-diagonal inward.
   */
  const lateralTargetAt = (n: NearestOnPolyline, dist: number): number => {
    const half = stationHalf(n);
    let targetY = bedYAt(n);
    if (banks) {
      const bank = Math.max(
        -bankLimit,
        Math.min(bankLimit, lerpNode(banks, n))
      );
      if (bank !== 0) {
        // Same sign as the track surface: `y = centre + right·lateral`, and a
        // positive bank tilts the right vector up.
        const lat = Math.sign(n.signed) * Math.min(dist, half);
        targetY += lat * Math.sin(bank);
      }
    }
    const flatEdge = half + shoulder;
    if (bermBand > 0 && dist > flatEdge) {
      const t = Math.min(1, (dist - flatEdge) / bermBand);
      targetY += bermH * (t * t * (3 - 2 * t));
    }
    return targetY;
  };

  const pick = (wx: number, wz: number): NearestOnPolyline | null => {
    if (index && overlap === 'closest-elevation') {
      const passes = nearestCorridorPasses(index, wx, wz, passSep);
      if (passes.length === 0) return null;
      if (passes.length === 1) return passes[0]!;
      const cur = sampleHeightAt(sampler, wx, wz);
      // Band priority: a pass whose full-weight bed contains the texel always
      // beats a pass that only reaches it through its falloff feather. The
      // reach grows with the widest adaptive falloff on the circuit, so a
      // distant arm whose bed sits near the natural ground can otherwise win
      // by elevation and stamp its feather ON TOP of the local road bed.
      let bestSolid: NearestOnPolyline | null = null;
      let bestSolidErr = 0;
      let bestFeather: NearestOnPolyline | null = null;
      let bestFeatherErr = 0;
      for (const p of passes) {
        const solid = solidEdgeAt(p);
        const fallSt = fallAt ? lerpNode(fallAt, p) : fall;
        if (p.dist >= solid + fallSt) continue;
        const err = Math.abs(bedYAt(p) - cur);
        if (p.dist < solid) {
          if (!bestSolid || err < bestSolidErr) {
            bestSolid = p;
            bestSolidErr = err;
          }
        } else if (!bestFeather || err < bestFeatherErr) {
          bestFeather = p;
          bestFeatherErr = err;
        }
      }
      return bestSolid ?? bestFeather;
    }
    const n = index
      ? nearestOnCorridor(index, wx, wz)
      : nearestOnPolyline(path, wx, wz);
    return n && n.dist < reach ? n : null;
  };

  const carved = applyHeightBrush(
    sampler,
    {
      minX: aabb.minX,
      maxX: aabb.maxX,
      minZ: aabb.minZ,
      maxZ: aabb.maxZ,
      evalAt(wx, wz) {
        if (opts.skipAt?.(wx, wz)) return null;
        if (discs) {
          for (const d of discs) {
            if (Math.hypot(wx - d.x, wz - d.z) <= d.r) return null;
          }
        }
        if (ribbons) {
          for (const rib of ribbons) {
            const nRib = nearestOnPolyline(rib.path, wx, wz);
            if (nRib && nRib.dist <= rib.half) return null;
          }
        }

        const n = pick(wx, wz);
        if (!n) return null;

        // Flying over this station: the ground below belongs to the valley.
        const ground = maskAt ? maskAt(n.arc) : 1;
        if (ground <= 0) return null;

        const solid = solidEdgeAt(n);
        const fallSt = fallAt ? lerpNode(fallAt, n) : fall;
        if (n.dist >= solid + fallSt) return null;

        const targetY = lateralTargetAt(n, n.dist);

        if (guardY !== undefined && Number.isFinite(guardY)) {
          const cur = sampleHeightAt(sampler, wx, wz);
          if (targetY > cur && cur < guardY) return null;
        }

        let weight = ground;
        if (n.dist > solid) {
          const t = (n.dist - solid) / fallSt;
          weight *= 1 - ease5(t);
        }
        return { targetY, weight };
      },

      /**
       * Cell-aware wall clamp. A texel centred in the falloff band holds
       * `natural + (design − natural)·w`; in a deep mountain cut that is
       * metres above the bed, and its bilinear stencil reaches a full texel
       * diagonal toward the axis — the reconstructed terrain climbs over the
       * run-off and bed edge (the dirt ridge hugging a track through a
       * hillside). Pull such texels down to the design surface at the
       * stencil's corridor-facing edge, lower-only: embankment fills, flats
       * and below-water texels never move.
       */
      guardAt(wx, wz) {
        if (opts.skipAt?.(wx, wz)) return null;
        if (discs) {
          for (const d of discs) {
            if (Math.hypot(wx - d.x, wz - d.z) <= d.r) return null;
          }
        }
        if (ribbons) {
          for (const rib of ribbons) {
            const nRib = nearestOnPolyline(rib.path, wx, wz);
            if (nRib && nRib.dist <= rib.half) return null;
          }
        }

        const n = pick(wx, wz);
        if (!n) return null;
        // Viaduct ramps fade the carve on purpose — a hard clamp there would
        // fight the fade. Only fully grounded stations guarantee the bed.
        const ground = maskAt ? maskAt(n.arc) : 1;
        if (ground < 1) return null;

        const solid = solidEdgeAt(n);
        // Full-weight centres are exact already; clamping them would shave
        // banked beds (the tilt is part of the design surface).
        if (n.dist < solid) return null;

        const dNear = Math.max(0, n.dist - texelInfluenceReach(sampler));
        if (dNear >= solid) return null;
        return { targetY: lateralTargetAt(n, dNear), weight: 1 };
      },
    },
    opts.owner ? { owner: opts.owner } : undefined
  );

  let any = carved;
  if (maskAt) {
    if (
      carveViaductDeckClearance(sampler, {
        path,
        arcs,
        profile,
        sink,
        width: maxHalf * 2,
        falloff: fall,
        maskAt,
        owner: opts.owner,
      })
    ) {
      any = true;
    }
  }

  return any;
}

/**
 * Deck-height interpolation along one flying run: `u` ∈ [0,1] of the run's
 * own arc → linear interpolation between the run nodes' design heights.
 * (`carveBridgeDeckClearance` samples `deckYAt` per texel, so the lookup is a
 * short advancing scan, not a global binary search.)
 */
function runDeckYAt(
  runArcs: readonly number[],
  runY: readonly number[]
): (u: number) => number {
  const total = runArcs[runArcs.length - 1] ?? 0;
  return (u: number): number => {
    const a = Math.min(1, Math.max(0, u)) * total;
    let i = 1;
    while (i < runArcs.length - 1 && runArcs[i]! < a) i++;
    const span = Math.max(runArcs[i]! - runArcs[i - 1]!, 1e-6);
    const t = Math.min(1, Math.max(0, (a - runArcs[i - 1]!) / span));
    return runY[i - 1]! + (runY[i]! - runY[i - 1]!) * t;
  };
}

/**
 * Viaduct deck clearance for `<Road flatten-viaduct-clearance>` corridors.
 *
 * The bed carve deliberately skips stations that fly above the natural ground
 * (the valley, forest and lake under the span stay untouched) — but nothing
 * in that contract keeps **lateral** terrain out of the deck: on a span
 * crossing a hillside, the mountain beside the centerline pokes through the
 * deck slab and the ribbon edges. Same for the fade ramps at each abutment,
 * where the partial carve weight can leave cut-side terrain above the bed.
 *
 * This runs a lower-only {@link carveBridgeDeckClearance} along every run of
 * stations that is not fully grounded (fade + flying): terrain above the
 * design profile (minus the deck undercut) is cut inside the deck footprint,
 * everything already below it — the valley floor, the water, the embankment
 * fills — stays exactly as authored. Guard clamp included (same stencil
 * contract as the corridor stamp); writes append to the owner journal.
 */
function carveViaductDeckClearance(
  sampler: HeightSampler,
  opts: {
    path: number[];
    arcs: readonly number[];
    profile: readonly number[];
    sink: number;
    width: number;
    falloff: number;
    maskAt: (arc: number) => number;
    owner?: string;
  }
): boolean {
  const { path, arcs, profile, sink, maskAt } = opts;
  const nodeCount = path.length / 2;

  let runPath: number[] = [];
  let runArcs: number[] = [];
  let runY: number[] = [];
  let any = false;

  const flushRun = (): void => {
    if (runPath.length < 4) {
      runPath = [];
      runArcs = [];
      runY = [];
      return;
    }
    if (
      carveBridgeDeckClearance(sampler, {
        path: runPath,
        width: opts.width,
        falloff: opts.falloff,
        deckYAt: runDeckYAt(runArcs, runY),
        owner: opts.owner,
      })
    ) {
      any = true;
    }
    runPath = [];
    runArcs = [];
    runY = [];
  };

  for (let i = 0; i < nodeCount; i++) {
    if (maskAt(arcs[i]!) >= 1) {
      flushRun();
      continue;
    }
    const x = path[i * 2]!;
    const z = path[i * 2 + 1]!;
    if (runArcs.length > 0) {
      const px = runPath[runPath.length - 2]!;
      const pz = runPath[runPath.length - 1]!;
      runArcs.push(runArcs[runArcs.length - 1]! + Math.hypot(x - px, z - pz));
    } else {
      runArcs.push(0);
    }
    runPath.push(x, z);
    runY.push(profile[i]! - sink);
  }
  flushRun();
  return any;
}

/** Terrain must stay at least this far under the deck walk surface (m). */
export const BRIDGE_DECK_UNDERCUT_M = 0.15;

/** Extra metres each side of the painted deck lane included in the cut. */
export const BRIDGE_CLEARANCE_WIDTH_BONUS = 1.2;

export interface BridgeClearanceOpts {
  /** Span polyline `[x0,z0,…]` in field-local coords (Way → Way). */
  path: number[];
  /** Deck lane width (m) at full weight. */
  width: number;
  /** Lateral blend back to natural relief (m). */
  falloff: number;
  /** Field-local Y of the deck walk surface at arc fraction `u` ∈ [0,1]. */
  deckYAt(u: number): number;
  /** Clearance kept under the deck; defaults to {@link BRIDGE_DECK_UNDERCUT_M}. */
  undercut?: number;
  /** Journal owner — same id as the road's corridor stamp (appends to it). */
  owner?: string;
}

/**
 * Cut terrain that pokes through the span so only the abutment tips stay
 * buried. Lower-only: a deck that arcs metres above the water leaves the
 * channel untouched, and no amount of contour can dam the river. Terrain that
 * already sits below the deck is left exactly as authored.
 */
export function carveBridgeDeckClearance(
  sampler: HeightSampler,
  opts: BridgeClearanceOpts
): boolean {
  const path = opts.path;
  if (path.length < 4) return false;

  const halfWidth =
    minEffectiveWidth(sampler, Math.max(opts.width, 0.1), 1) / 2;
  const fall = minEffectiveFalloff(sampler, Math.max(opts.falloff, 0.01), 1);
  const reach = halfWidth + fall;
  const undercut = opts.undercut ?? BRIDGE_DECK_UNDERCUT_M;

  const nodes = path.length / 2;
  const arcs: number[] = [0];
  for (let i = 1; i < nodes; i++) {
    arcs.push(
      arcs[i - 1]! +
        Math.hypot(
          path[i * 2]! - path[(i - 1) * 2]!,
          path[i * 2 + 1]! - path[(i - 1) * 2 + 1]!
        )
    );
  }
  const total = arcs[nodes - 1]!;
  if (!(total > 1e-6)) return false;

  const aabb = corridorAabb(path, reach);
  if (!aabb) return false;

  return applyHeightBrush(
    sampler,
    {
      minX: aabb.minX,
      maxX: aabb.maxX,
      minZ: aabb.minZ,
      maxZ: aabb.maxZ,
      mode: 'lower',
      evalAt(wx, wz) {
        const n = nearestOnPolyline(path, wx, wz);
        if (!n || n.dist >= reach) return null;

        const segLen = arcs[n.seg + 1]! - arcs[n.seg]!;
        const u = (arcs[n.seg]! + segLen * n.t) / total;
        const targetY = opts.deckYAt(u) - undercut;

        let weight = 1;
        if (n.dist > halfWidth) {
          const t = (n.dist - halfWidth) / fall;
          weight = 1 - t * t * (3 - 2 * t);
        }
        return { targetY, weight };
      },
      // Cell-aware: a bank texel left by the feathered weight can still poke
      // through the fascia/deck edge — clamp it to the deck underside at the
      // stencil's corridor-facing edge (lower-only, like the primary).
      guardAt(wx, wz) {
        const n = nearestOnPolyline(path, wx, wz);
        if (!n || n.dist >= reach) return null;
        if (n.dist < halfWidth) return null;
        const dNear = Math.max(0, n.dist - texelInfluenceReach(sampler));
        if (dNear >= halfWidth) return null;
        const segLen = arcs[n.seg + 1]! - arcs[n.seg]!;
        const u = (arcs[n.seg]! + segLen * n.t) / total;
        return { targetY: opts.deckYAt(u) - undercut, weight: 1 };
      },
    },
    opts.owner ? { owner: opts.owner } : undefined
  );
}

/**
 * Landward seat length (m) behind each Way — the only long flatten for bridges.
 * (Legacy name kept: texel floor / corridor window still key off this.)
 */
export const BRIDGE_APPROACH_METERS = 8;

/** Solid ground behind each Way (away from span) included in the approach stub. */
export const BRIDGE_LANDWARD_METERS = 8;

/**
 * How far stubs may bite **into** the span (m). Needs enough reach to terrace
 * the beach under the abutment, but with clamped approach falloff must stay
 * well short of mid-channel (native spans ~18–24 m).
 */
export const BRIDGE_INTO_SPAN_METERS = 3.5;

/** Extra bed width on approach stubs so abutments get a real seat pad. */
export const BRIDGE_APPROACH_WIDTH_BONUS = 2;

/**
 * Skip the heightfield approach flatten when texel ≥ this (m). On a lattice
 * that coarse `minEffectiveWidth` expands a 6 m bed to tens of metres and
 * stamps a sand plug across the whole river; the lip then comes from
 * TerrainPads alone. {@link carveBridgeDeckClearance} still runs — it only
 * lowers, so it cannot plug anything.
 */
export const BRIDGE_SKIP_CARVE_TEXEL_M = 10;

/** True when the sampler is fine enough for a narrow abutment flatten. */
export function shouldCarveBridgeApproaches(sampler: HeightSampler): boolean {
  return samplerTexelStep(sampler) < BRIDGE_SKIP_CARVE_TEXEL_M;
}

/** Minimum approach length in sampler texels (so flatten hits ≥1 centre). */
export const BRIDGE_APPROACH_MIN_TEXELS = 2.5;

/** Soft shoulder for bank lips — ≥ this many texels of falloff. */
export const BRIDGE_APPROACH_FALLOFF_TEXELS = 2.0;

/** Approach terrace window capped relative to approach length. */
export const BRIDGE_APPROACH_WINDOW_FACTOR = 1.75;

/** Gentler grade on bank stubs (~12%) — less cut into pad lips. */
export const BRIDGE_APPROACH_MAX_GRADE = 0.12;

/** Lighter sink than artery — soft lip, not a trench into the river. */
export const BRIDGE_APPROACH_PLATFORM_SINK = DEFAULT_ROAD_PLATFORM_SINK * 0.5;

/** Ribbon sits this far above the probed deck walk surface (m). */
export const BRIDGE_RIBBON_CLEARANCE = 0.02;

/**
 * Approach metres: authored (or default) floored to cover enough heightmap
 * texels. Coarse maps (world/64 ≈ 32 m/texel) need longer stubs than 6 m.
 */
export function effectiveBridgeApproachMeters(
  sampler: HeightSampler,
  authored = BRIDGE_APPROACH_METERS
): number {
  const step = samplerTexelStep(sampler);
  return Math.max(authored, step * BRIDGE_APPROACH_MIN_TEXELS);
}

/**
 * Corridor opts tuned for bridge bank stubs: texel-aware width/falloff,
 * short terrace window, soft sink, gentle grade. Never used on mid-span.
 */
export function bridgeApproachCorridorOpts(
  sampler: HeightSampler,
  base: RoadCorridorOpts & { approachMeters?: number }
): RoadCorridorOpts & { approachMeters: number } {
  const approach = effectiveBridgeApproachMeters(
    sampler,
    base.approachMeters ?? BRIDGE_APPROACH_METERS
  );
  // Do NOT inherit artery flatten-falloff (often 16 m+): nearestOnPolyline
  // clamps past the stub tip, so a wide falloff + flatTargetY paints a disc
  // that plugs the whole river when Ways sit ~native-span apart.
  const falloff = minEffectiveFalloff(
    sampler,
    Math.min(
      base.falloff > 0 ? base.falloff : approach * 0.35,
      approach * 0.35
    ),
    BRIDGE_APPROACH_FALLOFF_TEXELS
  );
  const width = minEffectiveWidth(sampler, base.width, 1.5);
  const window = Math.min(
    base.window,
    Math.max(approach * BRIDGE_APPROACH_WINDOW_FACTOR, falloff * 2)
  );
  return {
    path: base.path,
    width,
    falloff,
    window,
    maxGrade:
      base.maxGrade !== undefined && base.maxGrade > 0
        ? Math.min(base.maxGrade, BRIDGE_APPROACH_MAX_GRADE)
        : BRIDGE_APPROACH_MAX_GRADE,
    platformSink: BRIDGE_APPROACH_PLATFORM_SINK,
    approachMeters: approach,
  };
}

/**
 * Walk `path` and keep a prefix / suffix of at most `approachM` metres of arc.
 * Mid-span is omitted so a bridge corridor cannot refill a river channel.
 */
export function clipPathApproaches(
  path: number[],
  approachM: number
): number[][] {
  const n = path.length / 2;
  if (n < 2 || approachM <= 0) return [];
  const arcs: number[] = [0];
  for (let i = 1; i < n; i++) {
    arcs.push(
      arcs[i - 1]! +
        Math.hypot(
          path[i * 2]! - path[(i - 1) * 2]!,
          path[i * 2 + 1]! - path[(i - 1) * 2 + 1]!
        )
    );
  }
  const total = arcs[n - 1]!;
  if (total < 1e-6) return [];

  const stubs: number[][] = [];
  const pushStub = (fromArc: number, toArc: number) => {
    const stub: number[] = [];
    for (let i = 0; i < n; i++) {
      const a = arcs[i]!;
      if (a < fromArc - 1e-6) continue;
      if (a > toArc + 1e-6) break;
      stub.push(path[i * 2]!, path[i * 2 + 1]!);
    }
    // Ensure at least the endpoint on the clipped side.
    if (stub.length < 4) {
      // interpolate ends
      const sampleAt = (target: number): [number, number] => {
        for (let i = 0; i < n - 1; i++) {
          if (arcs[i + 1]! + 1e-9 < target) continue;
          const span = arcs[i + 1]! - arcs[i]!;
          const t = span > 1e-9 ? (target - arcs[i]!) / span : 0;
          return [
            path[i * 2]! + (path[(i + 1) * 2]! - path[i * 2]!) * t,
            path[i * 2 + 1]! + (path[(i + 1) * 2 + 1]! - path[i * 2 + 1]!) * t,
          ];
        }
        return [path[(n - 1) * 2]!, path[(n - 1) * 2 + 1]!];
      };
      const a = sampleAt(fromArc);
      const b = sampleAt(toArc);
      stub.length = 0;
      stub.push(a[0], a[1], b[0], b[1]);
    }
    if (stub.length >= 4) stubs.push(stub);
  };

  const tip = Math.min(approachM, total * 0.45);
  pushStub(0, tip);
  if (total > tip * 2 + 1) {
    pushStub(total - tip, total);
  }
  return stubs;
}

/**
 * Build approach stubs: solid ground **behind** each Way (landward) plus a
 * tiny bite into the span. `intoM` must stay small — long into-span + raise
 * lip fills the river under the deck.
 */
export function bridgeApproachStubs(
  path: number[],
  intoM: number,
  landwardM = BRIDGE_LANDWARD_METERS
): number[][] {
  const n = path.length / 2;
  if (n < 2) return [];
  const x0 = path[0]!;
  const z0 = path[1]!;
  const x1 = path[path.length - 2]!;
  const z1 = path[path.length - 1]!;
  const dx = x1 - x0;
  const dz = z1 - z0;
  const len = Math.hypot(dx, dz);
  if (len < 1e-6) return [];
  const ux = dx / len;
  const uz = dz / len;
  // Never more than ~12% of span each side — leave the channel alone.
  const into = Math.min(
    Math.max(0, intoM),
    len * 0.12,
    BRIDGE_INTO_SPAN_METERS
  );
  const back = Math.max(0, landwardM);
  if (into <= 0 && back <= 0) return [];

  const stubs: number[][] = [];
  // Start tip: landward ← Way → tiny into-span
  stubs.push([
    x0 - ux * back,
    z0 - uz * back,
    x0,
    z0,
    x0 + ux * into,
    z0 + uz * into,
  ]);
  // End tip: tiny into-span ← Way → landward
  stubs.push([
    x1 - ux * into,
    z1 - uz * into,
    x1,
    z1,
    x1 + ux * back,
    z1 + uz * back,
  ]);
  return stubs;
}

/**
 * Flatten only the approach stubs of a bridge path.
 * Uses texel-aware seat length + soft bank opts so coarse heightmaps
 * still get a real lip without terracing the mid-span.
 *
 * Pass {@link RoadCorridorOpts.flatTargetY} (field-local metres) so **both**
 * stubs stamp the same deck plane — required for a level bridge seat.
 * Returns true if any stub changed the sampler.
 */
export function carveRoadApproaches(
  sampler: HeightSampler,
  opts: RoadCorridorOpts & {
    approachMeters?: number;
    landwardMeters?: number;
    intoSpanMeters?: number;
  }
): boolean {
  // Coarse heightmaps cannot seat a narrow lip — expanding width to ≥1.5
  // texels fills the channel under the span. Rely on TerrainPad + River.
  if (!shouldCarveBridgeApproaches(sampler)) return false;
  // One revert for the whole set — each stub then appends to the same journal.
  if (opts.owner) revertHeightBrush(sampler, opts.owner);
  const tuned = bridgeApproachCorridorOpts(sampler, opts);
  const landward = opts.landwardMeters ?? BRIDGE_LANDWARD_METERS;
  const into = opts.intoSpanMeters ?? BRIDGE_INTO_SPAN_METERS;
  const stubs = bridgeApproachStubs(opts.path, into, landward);
  const flatY = opts.flatTargetY;
  // Wider seat under abutments (still stub-local — mid-span untouched).
  const seatWidth = tuned.width + BRIDGE_APPROACH_WIDTH_BONUS;
  let any = false;
  for (const stub of stubs) {
    if (
      carveRoadCorridor(sampler, {
        path: stub,
        width: seatWidth,
        falloff: tuned.falloff,
        window: tuned.window,
        maxGrade: tuned.maxGrade,
        // Fixed falloff on bridge seats: the adaptive widening exists for deep
        // cuts, and the deepest "cut" near a bridge is the river channel — a
        // widened stub paints a plug over it.
        maxCutSlope: 0,
        platformSink:
          flatY !== undefined && Number.isFinite(flatY)
            ? 0
            : tuned.platformSink,
        flatTargetY: flatY,
        noRaiseBelowY: opts.noRaiseBelowY,
        preserveDiscs: opts.preserveDiscs,
        preserveRibbons: opts.preserveRibbons,
        owner: opts.owner,
        appendToOwner: true,
      })
    ) {
      any = true;
    }
  }
  return any;
}
