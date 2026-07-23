import { describe, expect, it } from 'bun:test';
import { resolveVariationSpec } from '../../../src/plugins/spawn-variation/resolve';
import { getVariationPreset } from '../../../src/plugins/spawn-variation/presets';

describe('resolveVariationSpec', () => {
  for (const profile of ['tree', 'foliage', 'default']) {
    it(`profile '${profile}' without attrs uses profile default preset`, () => {
      const spec = resolveVariationSpec({}, profile);
      if (profile === 'tree') expect(spec.preset).toBe('tree');
      else if (profile === 'foliage') expect(spec.preset).toBe('foliage');
      else expect(spec.preset).toBe('none');
    });
  }

  for (const preset of ['none', 'tree', 'rock', 'foliage'] as const) {
    it(`variation="${preset}" overrides group profile`, () => {
      const spec = resolveVariationSpec({ variation: preset }, 'tree');
      expect(spec.preset).toBe(preset);
    });
  }

  for (let spatial = 0; spatial <= 10; spatial++) {
    it(`clamps variation-spatial=${spatial / 10} into 0..1`, () => {
      const spec = resolveVariationSpec(
        { 'variation-spatial': String(spatial / 10) },
        'tree'
      );
      expect(spec.spatial).toBeGreaterThanOrEqual(0);
      expect(spec.spatial).toBeLessThanOrEqual(1);
    });
  }

  it('swaps inverted saturation range from attrs', () => {
    const spec = resolveVariationSpec(
      { 'saturation-min': '1.2', 'saturation-max': '0.8' },
      'none'
    );
    expect(spec.saturationMin).toBeCloseTo(0.8, 5);
    expect(spec.saturationMax).toBeCloseTo(1.2, 5);
  });

  it('hue-jitter-deg cannot go negative', () => {
    const spec = resolveVariationSpec({ 'hue-jitter-deg': '-5' }, 'rock');
    expect(spec.hueJitterDeg).toBe(0);
  });

  for (const key of [
    'brightness-min',
    'brightness-max',
    'contrast-min',
    'contrast-max',
  ] as const) {
    it(`invalid ${key} falls back to preset base`, () => {
      const base = getVariationPreset('tree');
      const spec = resolveVariationSpec({ [key]: 'not-a-number' }, 'tree');
      if (key.endsWith('min')) {
        expect(spec.brightnessMin).toBe(base.brightnessMin);
      }
    });
  }
});

describe('INSTANCE_VARIATION_UNIFORM_SCHEMA', () => {
  it('declares fragment float uniforms', async () => {
    const { INSTANCE_VARIATION_UNIFORM_SCHEMA } =
      await import('../../../src/plugins/spawn-variation/material-patch');
    expect(INSTANCE_VARIATION_UNIFORM_SCHEMA.fragment.uVarBrightness).toBe(
      'float'
    );
    expect(INSTANCE_VARIATION_UNIFORM_SCHEMA.fragment.uVarContrast).toBe(
      'float'
    );
  });
});
