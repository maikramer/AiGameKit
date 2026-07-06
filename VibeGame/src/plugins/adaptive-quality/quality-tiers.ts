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
}

/**
 * The per-tier policy. Read by the apply system and by feature systems when
 * they query "should I be on at this tier?".
 *
 * Design notes:
 *  - Max preserves every effect at full quality.
 *  - High trims the heaviest single-pass costs (SSAO half-res, point-shadow
 *    throttle) while keeping the look (AO is subtle at half-res; static torch
 *    shadows are visually identical when refreshed every 4th frame).
 *  - Medium additionally reduces god-ray samples and DoF bokeh.
 *  - Low is a survival tier: pixel ratio down to 0.8, mirror off, DoF off.
 */
export const TIER_PRESETS: readonly QualityTierPreset[] = [
  // Tier 0 — Max
  {
    pixelRatioScale: 1.0,
    ssaoHalfResolution: false,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 1.0,
    godRaysSamples: 80,
    pointShadowRefreshFrames: 1,
    waterMirror: true,
  },
  // Tier 1 — High
  {
    pixelRatioScale: 1.0,
    ssaoHalfResolution: true,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 0.85,
    godRaysSamples: 80,
    pointShadowRefreshFrames: 4,
    waterMirror: true,
  },
  // Tier 2 — Medium
  {
    pixelRatioScale: 0.9,
    ssaoHalfResolution: true,
    bloomMipmapBlur: true,
    dofBokehScaleScale: 0.7,
    godRaysSamples: 40,
    pointShadowRefreshFrames: 6,
    waterMirror: false,
  },
  // Tier 3 — Low
  {
    pixelRatioScale: 0.8,
    ssaoHalfResolution: true,
    bloomMipmapBlur: false,
    dofBokehScaleScale: 0.0,
    godRaysSamples: 24,
    pointShadowRefreshFrames: 8,
    waterMirror: false,
  },
];

/** Read the current tier for the active AdaptiveQuality entity (0 if none). */
export function getAdaptiveQualityTier(state: State): number {
  const arr = state.getComponent('adaptive-quality') as
    typeof AdaptiveQuality | undefined;
  if (!arr) return QualityTier.Max;
  // There is at most one AdaptiveQuality entity per scene; read its tier.
  for (let i = 0; i < arr.enabled.length; i++) {
    if (arr.enabled[i]) return arr.currentTier[i];
  }
  return QualityTier.Max;
}

/** True when adaptive quality is present AND enabled (i.e. tiers may be > 0). */
export function isAdaptiveQualityActive(state: State): boolean {
  const arr = state.getComponent('adaptive-quality') as
    typeof AdaptiveQuality | undefined;
  if (!arr) return false;
  for (let i = 0; i < arr.enabled.length; i++) {
    if (arr.enabled[i]) return true;
  }
  return false;
}
