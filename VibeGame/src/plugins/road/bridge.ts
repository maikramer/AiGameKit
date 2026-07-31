import { radToDeg } from '../../shared';

/**
 * Bridge span helpers — lip decision, deck contour, yaw/scale, span fit.
 * Approach carve sizing lives in {@link ./carve} (avoids import cycles);
 * the raycast probe that measures the walk surface is in {@link ./bridge-deck}.
 */

/** Authored mesh length along local +X before scale (m). */
export const BRIDGE_NATIVE_SPAN_M = 18;

/**
 * Local Y of the topmost deck geometry (parapet / rail crown) in shipped
 * bridge LODs — wood ≈ 1.95, stone ≈ 2.18 above the mesh origin.
 *
 * Only a pre-load estimate for the spawn transform. The walk surface sits
 * lower than the crown and follows a ramp→plateau→ramp contour, so the final
 * seat comes from the probed contour (`bridge-deck.ts` + `planDeckOriginY`).
 */
export const BRIDGE_DECK_LOCAL_Y = 2.15;

/** Banks within this gap (m) count as level → mean lip, no grading needed. */
export const BRIDGE_LIP_LEVEL_EPS = 0.4;

/** Bank must sit at least this far above mid-channel to count as solid ground. */
export const BRIDGE_BANK_ABOVE_CHANNEL = 0.75;

/**
 * How deep the abutment tips sink below the bank lip. The tips are the **only**
 * part of the deck allowed under ground: everything from the ramp up is exposed
 * and the terrain is cut to clear it (see `carveBridgeDeckClearance`).
 */
export const BRIDGE_TIP_EMBED_M = 0.2;

/** Crown may not rise above lip + this (mesh far too tall for the span). */
export const BRIDGE_MAX_CROWN_ABOVE_LIP = 4;

/** Cut one metre off the high bank — the reference unit of grading cost. */
export const BRIDGE_CUT_COST_PER_M = 1;

/** Fill costs more than cut: an embankment can creep toward the channel. */
export const BRIDGE_FILL_COST_PER_M = 1.5;

/** Cut/fill up to here reads as normal grading; beyond it the penalty bites. */
export const BRIDGE_COMFORT_CUT_M = 1.2;
export const BRIDGE_COMFORT_FILL_M = 0.9;

/** Quadratic penalty factor applied to cut/fill beyond the comfort depth. */
export const BRIDGE_OVERRUN_PENALTY = 2.5;

/** Low bank this close above the channel makes any fill a damming risk. */
export const BRIDGE_SHALLOW_BANK_M = 2;

/** Extra fill cost per metre of missing bank freeboard. */
export const BRIDGE_SHALLOW_FILL_PENALTY = 4;

export type BridgeLipStrategy = 'match-low' | 'match-high' | 'match-mean';

export interface BridgeLipPlan {
  lip: number;
  strategy: BridgeLipStrategy;
  y0: number;
  y1: number;
  midY: number;
  /** Metres the high bank must be cut down to reach `lip`. */
  cut: number;
  /** Metres the low bank must be filled up to reach `lip`. */
  fill: number;
  /** Weighted grading cost of this lip (lower is better). */
  cost: number;
}

export interface BridgeGradingCost {
  cut: number;
  fill: number;
  cost: number;
}

/**
 * Weighted cost of seating the deck at `lip` between banks `lo`/`hi`.
 *
 * Cut and fill are both real terrain work, so neither is banned outright —
 * they are priced. Fill is dearer than cut, grows quadratically past the
 * comfort depth, and gets a steep surcharge when the low bank barely clears
 * the channel (that is the case where an embankment dams the river).
 */
export function bridgeLipCost(
  lip: number,
  lo: number,
  hi: number,
  midY: number
): BridgeGradingCost {
  const cut = Math.max(0, hi - lip);
  const fill = Math.max(0, lip - lo);
  const shallow = Math.max(0, BRIDGE_SHALLOW_BANK_M - (lo - midY));
  const overrun = (v: number, comfort: number): number => {
    const x = Math.max(0, v - comfort);
    return x * x * BRIDGE_OVERRUN_PENALTY;
  };
  const cost =
    cut * BRIDGE_CUT_COST_PER_M +
    fill * (BRIDGE_FILL_COST_PER_M + shallow * BRIDGE_SHALLOW_FILL_PENALTY) +
    overrun(cut, BRIDGE_COMFORT_CUT_M) +
    overrun(fill, BRIDGE_COMFORT_FILL_M);
  return { cut, fill, cost };
}

/**
 * Pick a shared deck lip from landward bank heights + mid-channel sample.
 *
 * Raising one bank and lowering the other are weighed against each other via
 * {@link bridgeLipCost} instead of hard-coding one direction: level banks take
 * the mean, a solid pair splits the difference, and a low bank that sits just
 * above the water forces `match-low` (cut the high side) because filling there
 * would terrace sand into the channel.
 */
export function chooseBridgeLip(
  y0: number,
  y1: number,
  midY: number
): BridgeLipPlan {
  const mid = Number.isFinite(midY) ? midY : Math.min(y0, y1) - 3;
  const solid = (y: number): boolean => y >= mid + BRIDGE_BANK_ABOVE_CHANNEL;
  // Drop samples that are still on the river slope — treat as the other bank.
  let a = y0;
  let b = y1;
  if (!solid(a) && solid(b)) a = b;
  else if (!solid(b) && solid(a)) b = a;
  else if (!solid(a) && !solid(b)) {
    // Both in the cut — seat a little above water, not on a sand plug.
    const lip = mid + 1.5;
    return {
      lip,
      strategy: 'match-mean',
      y0,
      y1,
      midY: mid,
      cut: 0,
      fill: 0,
      cost: 0,
    };
  }

  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const plan = (lip: number, strategy: BridgeLipStrategy): BridgeLipPlan => ({
    lip,
    strategy,
    y0,
    y1,
    midY: mid,
    ...bridgeLipCost(lip, lo, hi, mid),
  });

  if (hi - lo <= BRIDGE_LIP_LEVEL_EPS) {
    return plan((a + b) * 0.5, 'match-mean');
  }

  const candidates: BridgeLipPlan[] = [
    plan(lo, 'match-low'),
    plan((lo + hi) * 0.5, 'match-mean'),
    plan(hi, 'match-high'),
  ];
  // Ties go to the lower lip — less fill near the water.
  let best = candidates[0]!;
  for (const c of candidates) {
    if (c.cost < best.cost - 1e-9) best = c;
  }
  return best;
}

/**
 * Among height samples on one bank, pick the lowest that is still solid above
 * the channel. Avoids chasing an arterial flatten spike that buries the deck.
 */
export function pickSolidBankY(samples: number[], midY: number): number {
  if (samples.length === 0) return 0;
  const mid = Number.isFinite(midY) ? midY : Math.min(...samples) - 3;
  const solid = samples.filter(
    (y) => Number.isFinite(y) && y >= mid + BRIDGE_BANK_ABOVE_CHANNEL
  );
  if (solid.length > 0) return Math.min(...solid);
  return Math.max(...samples.filter(Number.isFinite));
}

/**
 * Deck walk surface sampled at even arc fractions along the span, stored as
 * offsets from the deck entity origin (the mesh is only scaled in X, so a
 * vertical move of the entity shifts the whole contour rigidly).
 */
export type BridgeDeckContour = number[];

/** Replace probe misses with the nearest hit; null when nothing was hit. */
export function fillContourGaps(
  raw: Array<number | null>
): BridgeDeckContour | null {
  const n = raw.length;
  if (n < 2) return null;
  const out: number[] = new Array(n);
  let last: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = raw[i];
    if (v !== null && Number.isFinite(v)) last = v;
    out[i] = last ?? NaN;
  }
  if (last === null) return null;
  // Leading misses: back-fill from the first hit.
  let next: number | null = null;
  for (let i = n - 1; i >= 0; i--) {
    if (Number.isFinite(out[i]!)) next = out[i]!;
    else out[i] = next!;
  }
  return out;
}

/** Deck surface at arc fraction `u` ∈ [0,1] (linear between samples). */
export function deckContourAt(contour: BridgeDeckContour, u: number): number {
  const n = contour.length;
  if (n === 0) return 0;
  if (n === 1) return contour[0]!;
  const t = u <= 0 ? 0 : u >= 1 ? 1 : u;
  const p = t * (n - 1);
  const i = Math.min(n - 2, Math.floor(p));
  const f = p - i;
  return contour[i]! + (contour[i + 1]! - contour[i]!) * f;
}

/** Mean of both abutment tips — the reference height for seating. */
export function deckContourTipY(contour: BridgeDeckContour): number {
  if (contour.length === 0) return 0;
  return (contour[0]! + contour[contour.length - 1]!) * 0.5;
}

/** Highest point of the walk surface (crown of an arched deck). */
export function deckContourCrown(contour: BridgeDeckContour): number {
  let max = -Infinity;
  for (const y of contour) if (y > max) max = y;
  return Number.isFinite(max) ? max : 0;
}

/**
 * World Y for the deck entity origin so the abutment tips sit
 * {@link BRIDGE_TIP_EMBED_M} under the bank lip — the ramps and plateau then
 * rise clear of the ground on their own.
 *
 * Clamped both ways: the crown never buries under the lip (deck flatter than
 * the bank) and never overshoots {@link BRIDGE_MAX_CROWN_ABOVE_LIP} (mesh far
 * taller than the span deserves).
 */
export function planDeckOriginY(
  contour: BridgeDeckContour,
  lipY: number
): number {
  const tip = deckContourTipY(contour);
  const crown = deckContourCrown(contour);
  const wanted = lipY - BRIDGE_TIP_EMBED_M - tip;
  const minOrigin = lipY - crown;
  const maxOrigin = lipY + BRIDGE_MAX_CROWN_ABOVE_LIP - crown;
  if (wanted < minOrigin) return minOrigin;
  if (wanted > maxOrigin) return maxOrigin;
  return wanted;
}

/** Arc length of a flat `[x,z,…]` polyline. */
export function pathArcLength(path: number[]): number {
  let len = 0;
  for (let i = 2; i < path.length; i += 2) {
    len += Math.hypot(path[i]! - path[i - 2]!, path[i + 1]! - path[i - 1]!);
  }
  return len;
}

/** Point at arc distance `s` (clamped) along a flat `[x,z,…]` polyline. */
export function pathPointAtArc(
  path: number[],
  s: number
): { x: number; z: number } {
  const n = path.length / 2;
  if (n < 1) return { x: 0, z: 0 };
  if (n < 2) return { x: path[0]!, z: path[1]! };
  let acc = 0;
  for (let i = 0; i < n - 1; i++) {
    const ax = path[i * 2]!;
    const az = path[i * 2 + 1]!;
    const bx = path[(i + 1) * 2]!;
    const bz = path[(i + 1) * 2 + 1]!;
    const seg = Math.hypot(bx - ax, bz - az);
    if (s <= acc + seg || i === n - 2) {
      const t = seg > 1e-9 ? Math.min(1, Math.max(0, (s - acc) / seg)) : 0;
      return { x: ax + (bx - ax) * t, z: az + (bz - az) * t };
    }
    acc += seg;
  }
  return { x: path[(n - 1) * 2]!, z: path[(n - 1) * 2 + 1]! };
}

/**
 * Project (x,z) onto the path; return arc fraction 0..1 (clamped).
 * Used to lerp bank heights along the bridge ribbon.
 */
export function pathArcFraction(path: number[], x: number, z: number): number {
  const n = path.length / 2;
  if (n < 2) return 0;
  let bestD = Infinity;
  let bestArc = 0;
  let arc = 0;
  let total = 0;
  for (let i = 0; i < n - 1; i++) {
    const ax = path[i * 2]!;
    const az = path[i * 2 + 1]!;
    const bx = path[(i + 1) * 2]!;
    const bz = path[(i + 1) * 2 + 1]!;
    const abx = bx - ax;
    const abz = bz - az;
    const ab2 = abx * abx + abz * abz;
    const t =
      ab2 > 1e-12
        ? Math.max(0, Math.min(1, ((x - ax) * abx + (z - az) * abz) / ab2))
        : 0;
    const px = ax + abx * t;
    const pz = az + abz * t;
    const d = Math.hypot(x - px, z - pz);
    const segLen = Math.sqrt(ab2);
    if (d < bestD) {
      bestD = d;
      bestArc = arc + segLen * t;
    }
    arc += segLen;
    total += segLen;
  }
  return total > 1e-9 ? Math.max(0, Math.min(1, bestArc / total)) : 0;
}

/** Lerp bank heights by arc fraction along the authored path. */
export function bridgeDeckYAt(
  y0: number,
  y1: number,
  path: number[],
  x: number,
  z: number
): number {
  const t = pathArcFraction(path, x, z);
  return y0 + (y1 - y0) * t;
}

/** GLB scale X = world span / native mesh length. */
export function bridgeSpanScaleX(
  path: number[],
  nativeSpan = BRIDGE_NATIVE_SPAN_M
): number {
  const span = pathArcLength(path);
  if (span < 0.5 || !(nativeSpan > 0)) return 1;
  return span / nativeSpan;
}

/** Yaw degrees: local +X → path A→B (same convention as spawnBridgeDeck). */
export function bridgeYawDeg(path: number[]): number {
  if (path.length < 4) return 0;
  const x0 = path[0]!;
  const z0 = path[1]!;
  const x1 = path[path.length - 2]!;
  const z1 = path[path.length - 1]!;
  return radToDeg(Math.atan2(-(z1 - z0), x1 - x0));
}

/** Midpoint of path endpoints (deck spawn xz). */
export function bridgeMidXZ(path: number[]): { x: number; z: number } {
  if (path.length < 4) return { x: 0, z: 0 };
  return {
    x: (path[0]! + path[path.length - 2]!) * 0.5,
    z: (path[1]! + path[path.length - 1]!) * 0.5,
  };
}

/** Soft warn when authored span is far from native mesh length. */
export function bridgeSpanFitRatio(
  path: number[],
  nativeSpan = BRIDGE_NATIVE_SPAN_M
): number {
  if (!(nativeSpan > 0)) return 1;
  return pathArcLength(path) / nativeSpan;
}
