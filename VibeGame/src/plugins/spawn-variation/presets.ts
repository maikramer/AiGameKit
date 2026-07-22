import type { VariationPresetId, VariationVisualSpec } from './types';

const IDENTITY: VariationVisualSpec = {
  preset: 'none',
  hueJitterDeg: 0,
  saturationMin: 1,
  saturationMax: 1,
  brightnessMin: 1,
  brightnessMax: 1,
  contrastMin: 1,
  contrastMax: 1,
  spatial: 0,
};

const PRESETS: Record<VariationPresetId, VariationVisualSpec> = {
  none: IDENTITY,
  tree: {
    preset: 'tree',
    hueJitterDeg: 8,
    saturationMin: 0.9,
    saturationMax: 1.12,
    brightnessMin: 0.85,
    brightnessMax: 1.18,
    contrastMin: 0.9,
    contrastMax: 1.12,
    spatial: 0.4,
  },
  foliage: {
    preset: 'foliage',
    hueJitterDeg: 12,
    saturationMin: 0.85,
    saturationMax: 1.18,
    brightnessMin: 0.82,
    brightnessMax: 1.22,
    contrastMin: 0.88,
    contrastMax: 1.16,
    spatial: 0.5,
  },
  rock: {
    preset: 'rock',
    hueJitterDeg: 5,
    saturationMin: 0.85,
    saturationMax: 1.08,
    brightnessMin: 0.85,
    brightnessMax: 1.18,
    contrastMin: 0.9,
    contrastMax: 1.12,
    spatial: 0.45,
  },
};

export function getVariationPreset(id: VariationPresetId): VariationVisualSpec {
  return { ...PRESETS[id] };
}

/** Map spawn-group profile id → default variation preset. */
export function defaultVariationForGroupProfile(
  groupProfile: string
): VariationPresetId {
  if (groupProfile === 'tree') return 'tree';
  if (groupProfile === 'foliage') return 'foliage';
  return 'none';
}

export function normalizeVariationPresetId(
  raw: string | undefined | null
): VariationPresetId | null {
  if (raw == null) return null;
  const s = String(raw).trim().toLowerCase();
  if (s === 'none' || s === 'tree' || s === 'foliage' || s === 'rock') return s;
  return null;
}
