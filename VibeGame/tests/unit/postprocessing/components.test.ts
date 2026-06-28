import { describe, expect, it } from 'bun:test';
import { Color } from 'three';
import { Postprocessing } from 'vibegame';
import {
  HeightFogPass,
  type HeightFogOptions,
} from '../../../src/plugins/postprocessing/height-fog';

const MAX_ENTITIES = 100000;

const UINT8_FIELDS = [
  'enabled',
  'bloom',
  'chromaticAberration',
  'vignette',
  'aa',
  'toneMapping',
  'ssao',
  'depthOfField',
  'heightFog',
] as const;

const FLOAT32_FIELDS = [
  'bloomStrength',
  'bloomRadius',
  'bloomThreshold',
  'caStrength',
  'vignetteOffset',
  'vignetteDarkness',
  'toneMappingExposure',
  'ssaoIntensity',
  'ssaoRadius',
  'dofFocusDistance',
  'dofFocusRange',
  'dofBokehScale',
  'fogDensity',
  'fogHeight',
  'fogFalloff',
  'fogNoise',
] as const;

interface HeightFogInternals {
  uniforms: Record<string, { value: unknown }>;
}

describe('Postprocessing component', () => {
  it('stores every field as a MAX_ENTITIES-length typed array of the right kind', () => {
    for (const field of UINT8_FIELDS) {
      expect(Postprocessing[field]).toBeInstanceOf(Uint8Array);
      expect(Postprocessing[field]).toHaveLength(MAX_ENTITIES);
    }
    for (const field of FLOAT32_FIELDS) {
      expect(Postprocessing[field]).toBeInstanceOf(Float32Array);
      expect(Postprocessing[field]).toHaveLength(MAX_ENTITIES);
    }
    expect(Postprocessing.fogColor).toBeInstanceOf(Uint32Array);
    expect(Postprocessing.fogColor).toHaveLength(MAX_ENTITIES);
  });
});

describe('HeightFogPass defaults and overrides', () => {
  function fogUniforms(pass: HeightFogPass): HeightFogInternals['uniforms'] {
    return (pass as unknown as HeightFogInternals).uniforms;
  }

  it('applies the documented defaults when no options are given', () => {
    const camera = {
      isPerspectiveCamera: true,
    } as unknown as ConstructorParameters<typeof HeightFogPass>[0];
    const pass = new HeightFogPass(camera);
    const u = fogUniforms(pass);

    expect((u.uFogColor.value as Color).getHex()).toBe(0x10131a);
    expect(u.uFogDensity.value).toBeCloseTo(0.06, 5);
    expect(u.uFogHeight.value).toBe(2);
    expect(u.uFogFalloff.value).toBeCloseTo(0.15, 5);
    expect(u.uFogNoise.value).toBeCloseTo(0.5, 5);
    pass.dispose();
  });

  it('honours explicit HeightFogOptions over the defaults', () => {
    const options: HeightFogOptions = {
      color: 0xff8800,
      density: 0.2,
      height: 5,
      falloff: 0.4,
      noise: 0.9,
    };
    const camera = {
      isPerspectiveCamera: true,
    } as unknown as ConstructorParameters<typeof HeightFogPass>[0];
    const pass = new HeightFogPass(camera, options);
    const u = fogUniforms(pass);

    expect((u.uFogColor.value as Color).getHex()).toBe(0xff8800);
    expect(u.uFogDensity.value).toBe(0.2);
    expect(u.uFogHeight.value).toBe(5);
    expect(u.uFogFalloff.value).toBe(0.4);
    expect(u.uFogNoise.value).toBe(0.9);
    pass.dispose();
  });

  it('requires a composer depth texture', () => {
    const camera = {
      isPerspectiveCamera: true,
    } as unknown as ConstructorParameters<typeof HeightFogPass>[0];
    const pass = new HeightFogPass(camera);
    expect(pass.needsDepthTexture).toBe(true);
    pass.dispose();
  });
});
