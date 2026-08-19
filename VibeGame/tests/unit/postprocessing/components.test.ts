import { describe, expect, it } from 'bun:test';
import { Postprocessing } from 'vibegame';

import { MAX_ENTITIES } from '../../../src/core/ecs/constants';

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
