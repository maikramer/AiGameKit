import { describe, expect, it } from 'bun:test';
import {
  defaultVariationForGroupProfile,
  getVariationPreset,
  normalizeVariationPresetId,
} from '../../../src/plugins/spawn-variation/presets';
import type { VariationPresetId } from '../../../src/plugins/spawn-variation/types';

const PRESET_IDS: VariationPresetId[] = ['none', 'tree', 'foliage', 'rock'];

describe('variation presets', () => {
  for (const id of PRESET_IDS) {
    it(`getVariationPreset('${id}') returns a copy with matching preset id`, () => {
      const a = getVariationPreset(id);
      const b = getVariationPreset(id);
      expect(a).not.toBe(b);
      expect(a.preset).toBe(id);
      expect(a.spatial).toBeGreaterThanOrEqual(0);
      expect(a.spatial).toBeLessThanOrEqual(1);
      expect(a.hueJitterDeg).toBeGreaterThanOrEqual(0);
    });
  }

  for (const id of PRESET_IDS) {
    it(`preset ${id} keeps saturation/brightness/contrast ranges ordered`, () => {
      const p = getVariationPreset(id);
      expect(p.saturationMin).toBeLessThanOrEqual(p.saturationMax);
      expect(p.brightnessMin).toBeLessThanOrEqual(p.brightnessMax);
      expect(p.contrastMin).toBeLessThanOrEqual(p.contrastMax);
    });
  }

  const profileCases: [string, VariationPresetId][] = [
    ['tree', 'tree'],
    ['foliage', 'foliage'],
    ['rock', 'none'],
    ['prop', 'none'],
    ['', 'none'],
  ];
  for (const [profile, expected] of profileCases) {
    it(`defaultVariationForGroupProfile('${profile}') → ${expected}`, () => {
      expect(defaultVariationForGroupProfile(profile)).toBe(expected);
    });
  }

  const normalizeCases: [
    string | null | undefined,
    VariationPresetId | null,
  ][] = [
    ['none', 'none'],
    ['Tree', 'tree'],
    [' FOLIAGE ', 'foliage'],
    ['rock', 'rock'],
    ['invalid', null],
    [null, null],
    [undefined, null],
  ];
  for (const [raw, expected] of normalizeCases) {
    it(`normalizeVariationPresetId(${JSON.stringify(raw)}) → ${expected}`, () => {
      expect(normalizeVariationPresetId(raw)).toBe(expected);
    });
  }
});
