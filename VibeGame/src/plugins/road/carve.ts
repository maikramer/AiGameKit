import {
  applyHeightBrush,
  minEffectiveFalloff,
  minEffectiveWidth,
  samplerTexelStep,
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
  const flatY = opts.flatTargetY;
  const useFlat = flatY !== undefined && Number.isFinite(flatY);
  const profile = useFlat
    ? arcs.map(() => flatY!)
    : designRoadProfile(arcs, heights, opts.window, maxGrade);
  const sink = useFlat
    ? 0
    : opts.platformSink === undefined
      ? DEFAULT_ROAD_PLATFORM_SINK
      : Math.max(0, opts.platformSink);

  const aabb = corridorAabb(path, reach);
  if (!aabb) return false;
  const guardY = opts.noRaiseBelowY;
  const discs = opts.preserveDiscs;
  const ribbons = opts.preserveRibbons;

  return applyHeightBrush(sampler, {
    minX: aabb.minX,
    maxX: aabb.maxX,
    minZ: aabb.minZ,
    maxZ: aabb.maxZ,
    evalAt(wx, wz) {
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

      const n = nearestOnPolyline(path, wx, wz);
      if (!n || n.dist >= reach) return null;

      const p0 = profile[n.seg]!;
      const p1 = profile[n.seg + 1]!;
      const targetY = p0 + (p1 - p0) * n.t - sink;

      if (guardY !== undefined && Number.isFinite(guardY)) {
        const cur = sampleHeightAt(sampler, wx, wz);
        if (targetY > cur && cur < guardY) return null;
      }

      let weight = 1;
      if (n.dist > halfWidth) {
        const t = (n.dist - halfWidth) / fall;
        weight = 1 - t * t * (3 - 2 * t);
      }
      return { targetY, weight };
    },
  });
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

  return applyHeightBrush(sampler, {
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
  });
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
        platformSink:
          flatY !== undefined && Number.isFinite(flatY)
            ? 0
            : tuned.platformSink,
        flatTargetY: flatY,
        noRaiseBelowY: opts.noRaiseBelowY,
        preserveDiscs: opts.preserveDiscs,
        preserveRibbons: opts.preserveRibbons,
      })
    ) {
      any = true;
    }
  }
  return any;
}
