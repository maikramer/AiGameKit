import { describe, expect, it } from 'bun:test';
import { Postprocessing } from '../../../src/plugins/postprocessing/components';

import { MAX_ENTITIES } from '../../../src/core/ecs/constants';

const ALL_KEYS = Object.keys(Postprocessing).sort();

describe('Postprocessing component — every field', () => {
  for (const key of ALL_KEYS) {
    it(`${key} is a typed array of length MAX_ENTITIES`, () => {
      const arr = Postprocessing[key as keyof typeof Postprocessing];
      expect(arr.length).toBe(MAX_ENTITIES);
      const ctor = arr.constructor.name;
      expect(['Uint8Array', 'Float32Array', 'Uint32Array']).toContain(ctor);
    });
  }

  for (let slot = 0; slot < 45; slot++) {
    it(`float tuning round-trip on entity ${slot}`, () => {
      Postprocessing.bloomStrength[slot] = 0.1 + slot * 0.01;
      Postprocessing.ssrOpacity[slot] = 0.2 + slot * 0.005;
      Postprocessing.fogDensity[slot] = 0.05 + slot * 0.001;
      expect(Postprocessing.bloomStrength[slot]).toBeCloseTo(
        0.1 + slot * 0.01,
        5
      );
      expect(Postprocessing.ssrOpacity[slot]).toBeCloseTo(
        0.2 + slot * 0.005,
        5
      );
      Postprocessing.bloomStrength[slot] = 0;
      Postprocessing.ssrOpacity[slot] = 0;
      Postprocessing.fogDensity[slot] = 0;
    });
  }
});

async function loadPostprocessingPlugin() {
  await import('../../../src/plugins/rendering/plugin');
  return import('../../../src/plugins/postprocessing/plugin');
}

describe('PostprocessingPlugin registration', () => {
  it('registers three named systems', async () => {
    const { PostprocessingPlugin } = await loadPostprocessingPlugin();
    expect(PostprocessingPlugin.systems).toHaveLength(3);
    expect(PostprocessingPlugin.systems!.map((s) => s.name)).toEqual([
      'PostprocessingBuildSystem',
      'PostprocessingEffectUpdateSystem',
      'FogSyncSystem',
    ]);
  });

  it('maps postprocessing component', async () => {
    const { PostprocessingPlugin } = await loadPostprocessingPlugin();
    expect(PostprocessingPlugin.components!.postprocessing).toBe(
      Postprocessing
    );
  });

  for (const [key, value] of [
    ['enabled', 1],
    ['bloom', 1],
    ['bloomStrength', 0.6],
    ['aa', 2],
    ['toneMapping', 1],
    ['ssr', 0],
    ['ssrMaxDistance', 180],
  ] as const) {
    it(`default postprocessing.${key} = ${value}`, async () => {
      const { PostprocessingPlugin } = await loadPostprocessingPlugin();
      const defaults = PostprocessingPlugin.config!.defaults!
        .postprocessing as Record<string, number>;
      expect(defaults[key]).toBe(value);
    });
  }

  for (const [name, code] of [
    ['off', 0],
    ['fxaa', 1],
    ['smaa', 2],
  ] as const) {
    it(`aa enum ${name} = ${code}`, async () => {
      const { PostprocessingPlugin } = await loadPostprocessingPlugin();
      const aa = PostprocessingPlugin.config!.enums!.postprocessing
        .aa as Record<string, number>;
      expect(aa[name]).toBe(code);
    });
  }

  for (const [name, code] of [
    ['off', 0],
    ['agx', 1],
    ['aces', 2],
    ['neutral', 3],
    ['reinhard', 4],
  ] as const) {
    it(`toneMapping enum ${name} = ${code}`, async () => {
      const { PostprocessingPlugin } = await loadPostprocessingPlugin();
      const tm = PostprocessingPlugin.config!.enums!.postprocessing
        .toneMapping as Record<string, number>;
      expect(tm[name]).toBe(code);
    });
  }
});

const BUILTIN_EFFECT_KEYS = [
  'smaa',
  'fxaa',
  'bloom',
  'vignette',
  'ssao',
  'depthOfField',
  'tonemapping',
  'chromaticAberration',
  'colorGrading',
  'filmGrain',
  'godRays',
  'ssr',
] as const;

describe('builtin effect registration', () => {
  for (const key of BUILTIN_EFFECT_KEYS) {
    it(`registers effect '${key}' after builtin-effects load`, async () => {
      await import('../../../src/plugins/postprocessing/builtin-effects');
      const { getEffectDefinitions } =
        await import('../../../src/plugins/postprocessing/effect-registry');
      const keys = getEffectDefinitions().map((d) => d.key);
      expect(keys).toContain(key);
    });
  }
});
