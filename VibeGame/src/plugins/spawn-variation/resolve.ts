import type { XMLValue } from '../../core';
import {
  defaultVariationForGroupProfile,
  getVariationPreset,
  normalizeVariationPresetId,
} from './presets';
import type { VariationPresetId, VariationVisualSpec } from './types';

function optNumber(raw: XMLValue | undefined, fallback: number): number {
  if (raw === undefined || raw === null || String(raw).trim() === '') {
    return fallback;
  }
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function swapIfInverted(lo: number, hi: number): [number, number] {
  return lo <= hi ? [lo, hi] : [hi, lo];
}

/**
 * Resolve visual variation from XML attrs + group profile hint.
 * Explicit `variation="…"` wins; otherwise profile maps tree→tree, foliage→foliage.
 */
export function resolveVariationSpec(
  attrs: Record<string, XMLValue>,
  groupProfile: string
): VariationVisualSpec {
  const fromAttr = normalizeVariationPresetId(
    attrs.variation !== undefined ? String(attrs.variation) : null
  );
  const preset: VariationPresetId =
    fromAttr ?? defaultVariationForGroupProfile(groupProfile);
  const base = getVariationPreset(preset);

  let hueJitterDeg = optNumber(attrs['hue-jitter-deg'], base.hueJitterDeg);
  hueJitterDeg = Math.max(0, hueJitterDeg);

  let [saturationMin, saturationMax] = swapIfInverted(
    optNumber(attrs['saturation-min'], base.saturationMin),
    optNumber(attrs['saturation-max'], base.saturationMax)
  );
  let [brightnessMin, brightnessMax] = swapIfInverted(
    optNumber(attrs['brightness-min'], base.brightnessMin),
    optNumber(attrs['brightness-max'], base.brightnessMax)
  );
  let [contrastMin, contrastMax] = swapIfInverted(
    optNumber(attrs['contrast-min'], base.contrastMin),
    optNumber(attrs['contrast-max'], base.contrastMax)
  );

  let spatial = optNumber(attrs['variation-spatial'], base.spatial);
  spatial = Math.min(1, Math.max(0, spatial));

  return {
    preset,
    hueJitterDeg,
    saturationMin,
    saturationMax,
    brightnessMin,
    brightnessMax,
    contrastMin,
    contrastMax,
    spatial,
  };
}
