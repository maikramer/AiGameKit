import { getGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';

export type VegetationSizeTier = 'small' | 'medium' | 'large';

export interface SizeTierScale {
  tier: VegetationSizeTier;
  scaleMin: number;
  scaleMax: number;
  /** Native height in metres (Y), when known. */
  heightM: number | null;
}

const TIER_SCALE: Record<
  VegetationSizeTier,
  { scaleMin: number; scaleMax: number }
> = {
  small: { scaleMin: 0.9, scaleMax: 1.4 },
  medium: { scaleMin: 1.0, scaleMax: 1.8 },
  large: { scaleMin: 1.1, scaleMax: 2.2 },
};

export function sizeTierFromHeight(heightM: number): VegetationSizeTier {
  if (heightM < 0.22) return 'small';
  if (heightM <= 0.35) return 'medium';
  return 'large';
}

/** Filename hints when GLB bounds are not loaded yet. */
export function sizeTierFromFilename(url: string): VegetationSizeTier | null {
  const base = (url.split('/').pop() ?? url).toLowerCase();
  if (
    base.includes('large') ||
    base.includes('tall') ||
    base.includes('_big')
  ) {
    return 'large';
  }
  if (
    base.includes('short') ||
    base.includes('small') ||
    base.includes('tiny')
  ) {
    return 'small';
  }
  return null;
}

export function resolveSizeTier(url: string): SizeTierScale {
  const bounds = getGltfLocalYBounds(url);
  let heightM: number | null = null;
  let tier: VegetationSizeTier;
  if (bounds) {
    heightM = Math.max(0, bounds.maxY - bounds.minY);
    tier = sizeTierFromHeight(heightM);
  } else {
    tier = sizeTierFromFilename(url) ?? 'medium';
  }
  const range = TIER_SCALE[tier];
  return {
    tier,
    scaleMin: range.scaleMin,
    scaleMax: range.scaleMax,
    heightM,
  };
}

/**
 * Apply optional author scale-min/max as a global multiplier on tier ranges.
 * When both omitted, returns tier range unchanged.
 */
export function applyPatchScaleOverride(
  tier: SizeTierScale,
  patchScaleMin: number | null,
  patchScaleMax: number | null
): { scaleMin: number; scaleMax: number } {
  if (patchScaleMin === null && patchScaleMax === null) {
    return { scaleMin: tier.scaleMin, scaleMax: tier.scaleMax };
  }
  // Author values replace defaults (not multiply) — same as legacy Vegetation.
  const lo = patchScaleMin ?? tier.scaleMin;
  const hi = patchScaleMax ?? tier.scaleMax;
  return lo <= hi
    ? { scaleMin: lo, scaleMax: hi }
    : { scaleMin: hi, scaleMax: lo };
}
