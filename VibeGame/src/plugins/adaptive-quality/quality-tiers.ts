import type { State } from '../../core';
import { AdaptiveQuality } from './components';

/**
 * Quality tiers. Tier 0 is "Max" — every visual lever at its full setting, no
 * degradation. Higher tiers progressively trade fidelity for frame budget.
 *
 * The levers are deliberately conservative: the scaler NEVER degrades
 * proactively, only reactively to sustained frame-time pressure, and it
 * restores the top tier the moment headroom returns.
 */
export const QualityTier = {
  Max: 0,
  High: 1,
  Medium: 2,
  Low: 3,
} as const;

export type QualityTierName = keyof typeof QualityTier;

export interface QualityTierPreset {
  /** Pixel ratio multiplier applied to the renderer's current cap. 1.0 = cap. */
  pixelRatioScale: number;
  /** SSAO half-resolution (N8AO) — cheaper at the cost of AO sharpness. */
  ssaoHalfResolution: boolean;
  /** Multiplier on configured SSAO intensity (0 ≈ skip AO cost visually). */
  ssaoIntensityScale: number;
  /** Bloom mipmap blur — cheaper single-tap when disabled. */
  bloomMipmapBlur: boolean;
  /** DoF bokeh scale multiplier (<1 = less blur cost). 0 disables DoF. */
  dofBokehScaleScale: number;
  /** God-ray sample count (lower = cheaper radial blur). */
  godRaysSamples: number;
  /** Point-light shadow update cadence: refresh every N frames. 1 = every
   *  frame (default). 0 disables point shadows entirely. */
  pointShadowRefreshFrames: number;
  /** Water planar mirror enabled (extra full scene render). */
  waterMirror: boolean;
  /** Multiplier on the configured SSR intensity. 0 disables the pass, which
   *  also skips its geometry pass and its ray march entirely. */
  ssrIntensityScale: number;
}

/**
 * The per-tier policy. Read by the apply system and by feature systems when
 * they query "should I be on at this tier?".
 *
 * Design notes:
 *  - Max preserves every effect at full quality.
 *  - High trims the heaviest single-pass costs (SSAO half-res, point-shadow
 *    throttle, leaner god-rays) while keeping the look.
 *  - Medium drops water mirror + DPR, softens DoF/god-rays further.
 *  - Low is a survival tier: DPR ~0.55, mirror off, DoF off, SSAO intensity 0,
 *    reflections off (the ray march is the most expensive thing left).
 */
export const TIER_PRESETS: readonly QualityTierPreset[] = [
  // Tier 0 — Max
  {
    pixelRatioScale: 1.0,
    ssaoHalfResolution: false,
    ssaoIntensityScale: 1.0,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 1.0,
    godRaysSamples: 48,
    pointShadowRefreshFrames: 1,
    waterMirror: true,
    ssrIntensityScale: 1.0,
  },
  // Tier 1 — High
  {
    pixelRatioScale: 1.0,
    ssaoHalfResolution: true,
    ssaoIntensityScale: 1.0,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 0.85,
    godRaysSamples: 32,
    pointShadowRefreshFrames: 4,
    waterMirror: true,
    ssrIntensityScale: 1.0,
  },
  // Tier 2 — Medium
  {
    pixelRatioScale: 0.85,
    ssaoHalfResolution: true,
    ssaoIntensityScale: 0.7,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 0.5,
    godRaysSamples: 24,
    pointShadowRefreshFrames: 6,
    waterMirror: false,
    ssrIntensityScale: 0.6,
  },
  // Tier 3 — Low
  {
    pixelRatioScale: 0.55,
    ssaoHalfResolution: true,
    ssaoIntensityScale: 0.0,
    bloomMipmapBlur: false,
    dofBokehScaleScale: 0.0,
    godRaysSamples: 16,
    pointShadowRefreshFrames: 8,
    waterMirror: false,
    ssrIntensityScale: 0.0,
  },
];

/** Quality mode names as used by the options UI and the `?quality=` query. */
export type QualityModeName = 'auto' | 'low' | 'medium' | 'high' | 'max';

/** Component value for each mode name (order matches `AdaptiveQuality.mode`). */
export const QUALITY_MODE_VALUES = {
  auto: 0,
  low: 1,
  medium: 2,
  high: 3,
  max: 4,
} as const satisfies Record<QualityModeName, number>;

/** Inverse mapping: component value → mode name. */
const QUALITY_MODE_NAMES = ['auto', 'low', 'medium', 'high', 'max'] as const;

/** Tier each forced mode pins (0=Max … 3=Low); Auto has no fixed tier. */
const MODE_PINNED_TIER = [-1, 3, 2, 1, 0] as const;

/** Find the active AdaptiveQuality entity id (single-entity component). */
function activeQualityEntity(
  state: State
): { arr: typeof AdaptiveQuality; eid: number } | null {
  const arr = state.getComponent('adaptive-quality') as
    typeof AdaptiveQuality | undefined;
  if (!arr) return null;
  for (let i = 0; i < arr.enabled.length; i++) {
    if (arr.enabled[i]) return { arr, eid: i };
  }
  return null;
}

/** Read the current tier for the active AdaptiveQuality entity (0 if none).
 *  A forced mode (Low/Medium/High/Max) pins its tier; Auto returns the tier
 *  the auto-scaler settled on. */
export function getAdaptiveQualityTier(state: State): number {
  const active = activeQualityEntity(state);
  if (!active) return QualityTier.Max;
  const mode = active.arr.mode[active.eid];
  if (mode > 0) return MODE_PINNED_TIER[mode] ?? QualityTier.Max;
  return active.arr.currentTier[active.eid];
}

/** Read the current quality mode name ('auto' when no entity exists). */
export function getQualityMode(state: State): QualityModeName {
  const active = activeQualityEntity(state);
  if (!active) return 'auto';
  return QUALITY_MODE_NAMES[active.arr.mode[active.eid]] ?? 'auto';
}

/** Force a quality mode at runtime (options UI, tests). The forced tier is
 *  DERIVED (never written into `currentTier`), so returning to Auto resumes
 *  from the tier the scaler had chosen. Returns false when the scene has no
 *  active `<AdaptiveQuality>` entity to configure. */
export function setQualityMode(state: State, mode: QualityModeName): boolean {
  const active = activeQualityEntity(state);
  if (!active) return false;
  const value = QUALITY_MODE_VALUES[mode];
  active.arr.mode[active.eid] = value;
  if (value > 0) {
    // A forced tier is immediately authoritative — reset the transition
    // cooldown so returning to Auto later starts a fresh evaluation window.
    active.arr.lastTransitionMs[active.eid] = performance.now();
  }
  return true;
}

/** `?quality=` override captured once at boot (the plugin initializes before
 *  the world XML creates the component entity, so the measure system applies
 *  this on its first update and clears it). */
let pendingQueryMode: QualityModeName | null = null;

/** Read `?quality=auto|low|medium|high|max` (alias: `aq`) from the page URL.
 *  Invalid values are ignored. Exported for tests. */
export function parseQualityQuery(url: string): QualityModeName | null {
  const q = url.indexOf('?');
  if (q === -1) return null;
  for (const pair of url.slice(q + 1).split('&')) {
    const eq = pair.indexOf('=');
    if (eq === -1) continue;
    const key = pair.slice(0, eq);
    if (key !== 'quality' && key !== 'aq') continue;
    const value = pair.slice(eq + 1).toLowerCase();
    if ((QUALITY_MODE_NAMES as readonly string[]).includes(value)) {
      return value as QualityModeName;
    }
  }
  return null;
}

/** Capture the query override (called from the plugin initialize). */
export function captureQualityQueryOverride(): void {
  if (typeof window === 'undefined' || typeof location === 'undefined') return;
  pendingQueryMode = parseQualityQuery(location.href);
}

/** Apply the captured query override once an entity exists. Returns true when
 *  an override was pending and got applied. */
export function applyPendingQualityOverride(state: State): boolean {
  if (!pendingQueryMode) return false;
  const mode = pendingQueryMode;
  pendingQueryMode = null;
  return setQualityMode(state, mode);
}

/** True when adaptive quality is present AND enabled (i.e. tiers may be > 0). */
export function isAdaptiveQualityActive(state: State): boolean {
  const active = activeQualityEntity(state);
  return active !== null;
}
