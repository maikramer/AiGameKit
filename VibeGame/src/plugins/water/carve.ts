import { applyHeightBrush, forEachTexelInAabb } from '../terrain/height-brush';
import {
  forEachCorridorSegment,
  nearestOnPolyline,
  segmentAabb,
} from '../terrain/corridor';
import type { HeightSampler } from '../terrain/height-sampler';
import { sampleHeightAt } from '../terrain/height-sampler';
import { pathAabb } from './path-utils';

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
  depth: number,
  carveMargin: number = 1.0
): boolean {
  // The carve can extend past the water disc by `carveMargin` so a beach/margin
  // of exposed basin floor shows between the waterline and the natural terrain.
  const carveR = radius * carveMargin;
  // O contorno orgânico pode exceder o raio médio em SHORE_SHAPE_AMPLITUDE —
  // o AABB tem de cobrir o overshoot ou os "cantos" do contorno ficam sem
  // carve (o clamp antigo cortava exatamente aqui).
  const reach = carveR * (1 + SHORE_SHAPE_AMPLITUDE);

  return applyHeightBrush(sampler, {
    minX: localX - reach,
    maxX: localX + reach,
    minZ: localZ - reach,
    maxZ: localZ + reach,
    mode: 'lower',
    evalAt(wx, wz) {
      const dx = wx - localX;
      const dz = wz - localZ;
      const dist = Math.sqrt(dx * dx + dz * dz);
      // Organic outline: the effective radius varies with angle, so the bowl is
      // an irregular pond, not a stamped circle. t = dist / shapeRadius(angle).
      const angle = Math.atan2(dz, dx);
      const effR = carveR * shapeRadius(angle, localX, localZ);
      const t2 = (dist * dist) / (effR * effR);
      if (t2 >= 1) return null;
      const bowlY = rimY - depth * Math.pow(1 - t2, BOWL_PROFILE_EXPONENT);
      return { targetY: bowlY, weight: 1 };
    },
  });
}

/**
 * Carve a river channel along a polyline, terrain-following. For each texel
 * within the path AABB, find the nearest point on the path axis, look up the
 * pre-sampled axis height there (`axisY`, linearly interpolated between path
 * nodes), and carve a transverse profile `axisY − depth·(1 − t²)^1.5` where
 * `t = lateralDist/(width/2)`.
 *
 * `axisHeights` (one terrain-axis height per path node, sampled once from the
 * **unmutated** sampler before carving) makes the carve idempotent: re-running
 * it doesn't sample the already-lowered floor and dig deeper each pass.
 *
 * Following the local axis height (rather than a single global rim) means:
 *  - high terrain → a deep channel cut `depth` below the local surface,
 *  - low terrain → a shallow/surface channel, no trench,
 *  - no flat-plane discontinuities where the terrain dips below a global floor.
 *
 * Heights only ever go down (min), so overlapping channels and pre-existing
 * valleys are safe.
 *
 * @param axisHeights  Terrain height at each path node (pre-sampled, unmutated).
 *                     Length must equal `path.length/2`. If empty, falls back to
 *                     a flat floor at `_rimY − depth` (legacy/global behaviour).
 * @returns true when at least one texel changed (false on a flat sampler).
 */
export function carveChannel(
  sampler: HeightSampler,
  path: number[],
  width: number,
  _rimY: number,
  depth: number,
  axisHeights: number[] = [],
  carveMargin: number = 1.0
): boolean {
  // The carve can extend past the water ribbon by `carveMargin` so a bank/margin
  // of exposed channel floor shows between the waterline and the natural terrain.
  const halfWidth = (width * carveMargin) / 2;
  const useAxis = axisHeights.length >= path.length / 2;
  const aabb = pathAabb(path, halfWidth);

  return applyHeightBrush(sampler, {
    minX: aabb.minX,
    maxX: aabb.maxX,
    minZ: aabb.minZ,
    maxZ: aabb.maxZ,
    mode: 'lower',
    evalAt(wx, wz) {
      const n = nearestOnPolyline(path, wx, wz);
      if (!n) return null;
      const tLat = n.dist / halfWidth;
      if (tLat >= 1) return null;
      const axisY = useAxis
        ? axisHeights[n.seg]! +
          n.t * (axisHeights[n.seg + 1]! - axisHeights[n.seg]!)
        : _rimY;
      const floorY =
        axisY - depth * Math.pow(1 - tLat * tLat, BOWL_PROFILE_EXPONENT);
      return { targetY: floorY, weight: 1 };
    },
  });
}

export interface RiverCarveOpts {
  /** Waterline width (m): the carved floor crosses the water surface at
   *  exactly ±width/2 — the water ribbon and the carve agree by construction. */
  width: number;
  /** Water depth below the surface at the channel axis (m). */
  channelDepth: number;
  /** Exposed carved-bank width outside the waterline, each side (m). */
  bankWidth: number;
  /** Bank crest height above the local water surface (m) — the freeboard that
   *  keeps the water visibly recessed inside the carve. */
  bankHeight: number;
  /** Feather band width outside the bank crest blending back to terrain (m). */
  featherWidth: number;
  /** Max the bank/feather may raise low terrain (m) — bigger deficits are left
   *  to read as waterfalls/cliffs instead of building aqueduct walls. */
  maxBankRaise: number;
}

/**
 * Carve a river channel along dense stations with a known water-surface
 * profile. The transverse profile has three zones (d = lateral distance):
 *
 *  - **Water bowl** (d ≤ width/2): `surface − channelDepth·(1−t²)^1.5` —
 *    depth at the axis, crossing the surface exactly at the waterline. Lower
 *    only, so pre-existing gorges are kept.
 *  - **Bank** (width/2 < d ≤ width/2+bankWidth): smoothstep rise from the
 *    surface up to `surface + bankHeight`. Cuts high terrain down (the exposed
 *    earth bank) AND raises low terrain up (capped) so the water is contained
 *    and always sits below a visible lip.
 *  - **Feather** (…+featherWidth): raise-only smoothstep from the crest back
 *    to nothing, so raised banks don't end in a wall.
 *
 * Unlike {@link carveChannel} (single global AABB, nearest-segment search per
 * texel — O(texels × segments), unusable for map-length rivers), this stamps
 * each segment into its own sub-AABB. Raises run first, lowers second, so a
 * bend's raised bank can never poke into a neighbouring segment's channel.
 *
 * `surface` holds one water-surface height per station (field-local), already
 * descending (see RiverChannel). The profile keys off the surface — not the
 * raw terrain — so the channel always contains the water.
 *
 * Returns true when at least one texel changed.
 */
export function carveRiverChannel(
  sampler: HeightSampler,
  stations: number[],
  surface: number[],
  opts: RiverCarveOpts
): boolean {
  const { data, maxHeight } = sampler;
  if (!data || maxHeight <= 0) return false;
  const nodeCount = stations.length / 2;
  if (nodeCount < 2 || surface.length < nodeCount) return false;

  const halfW = opts.width / 2;
  const bankW = Math.max(0.01, opts.bankWidth);
  const halfCarve = halfW + bankW;
  const outer = halfCarve + opts.featherWidth;

  let changed = false;

  /**
   * Per-segment stamp through the shared texel iterator (±1 margin — same
   * lattice contract as {@link applyHeightBrush}). Long rivers stay O(segments
   * × local texels), not O(global AABB × all segments).
   */
  const eachSegmentTexel = (
    reach: number,
    visit: (idx: number, lateral: number, surfY: number) => number | null
  ): void => {
    forEachCorridorSegment(stations, (ax, az, bx, bz, i) => {
      const sa = surface[i]!;
      const sb = surface[i + 1]!;
      const dx = bx - ax;
      const dz = bz - az;
      const lenSq = dx * dx + dz * dz;
      forEachTexelInAabb(
        sampler,
        segmentAabb(ax, az, bx, bz, reach),
        (idx, wx, wz) => {
          let t = lenSq === 0 ? 0 : ((wx - ax) * dx + (wz - az) * dz) / lenSq;
          t = t < 0 ? 0 : t > 1 ? 1 : t;
          const cx = ax + t * dx;
          const cz = az + t * dz;
          const d = Math.hypot(wx - cx, wz - cz);
          if (d >= reach) return;
          const next = visit(idx, d, sa + t * (sb - sa));
          if (next !== null) {
            data[idx] = next;
            changed = true;
          }
        }
      );
    });
  };

  // Bank/feather rise above the water surface at lateral d.
  const bankRise = (d: number): number => {
    if (d <= halfW) return 0;
    if (d <= halfCarve) {
      const s = (d - halfW) / bankW;
      return opts.bankHeight * (s * s * (3 - 2 * s));
    }
    const f = 1 - (d - halfCarve) / Math.max(opts.featherWidth, 0.01);
    return opts.bankHeight * (f * f * (3 - 2 * f));
  };

  // Pass 1 — banks + feather (raise, capped).
  eachSegmentTexel(outer, (idx, d, surfY) => {
    if (d <= halfW) return null;
    const cur = data[idx]! * maxHeight;
    const targetY = surfY + bankRise(d);
    if (cur >= targetY) return null;
    if (targetY - cur > opts.maxBankRaise) return null;
    return Math.min(1, Math.max(0, targetY / maxHeight));
  });

  // Pass 2 — water bowl + bank cut (lower only).
  eachSegmentTexel(halfCarve, (idx, d, surfY) => {
    let floorY: number;
    if (d <= halfW) {
      const tLat = d / halfW;
      floorY =
        surfY -
        opts.channelDepth * Math.pow(1 - tLat * tLat, BOWL_PROFILE_EXPONENT);
    } else {
      floorY = surfY + bankRise(d);
    }
    const target = Math.min(1, Math.max(0, floorY / maxHeight));
    if (data[idx]! <= target) return null;
    return target;
  });

  return changed;
}

/**
 * Lowest terrain height along both banks of the river path. Probes ~16 points
 * per bank (offset ±width/2 along the segment normal) and returns the minimum
 * so the water surface never leaks over a low bank. Analogous to `rimHeight`
 * for lakes but sampled along the polyline instead of a ring.
 */
export function rimHeightAlongPath(
  sampler: HeightSampler,
  path: number[],
  width: number
): number {
  let min = Infinity;
  const halfWidth = width / 2;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const bx = path[i + 2]!;
    const bz = path[i + 3]!;
    const dx = bx - ax;
    const dz = bz - az;
    const len = Math.hypot(dx, dz);
    if (len === 0) continue;
    const nx = -dz / len; // segment normal (perpendicular)
    const nz = dx / len;
    for (let s = 0; s <= 16; s++) {
      const f = s / 16;
      const px = ax + dx * f;
      const pz = az + dz * f;
      const hPlus = sampleHeightAt(
        sampler,
        px + nx * halfWidth,
        pz + nz * halfWidth
      );
      const hMinus = sampleHeightAt(
        sampler,
        px - nx * halfWidth,
        pz - nz * halfWidth
      );
      if (hPlus < min) min = hPlus;
      if (hMinus < min) min = hMinus;
    }
  }
  return Number.isFinite(min) ? min : 0;
}
