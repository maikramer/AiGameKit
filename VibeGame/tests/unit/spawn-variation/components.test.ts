import { describe, expect, it } from 'bun:test';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import { SpawnVariation } from '../../../src/plugins/spawn-variation/components';

const FIELDS = [
  'colorR',
  'colorG',
  'colorB',
  'brightness',
  'contrast',
] as const;

describe('SpawnVariation component', () => {
  for (const field of FIELDS) {
    it(`${field} is Float32Array(MAX_ENTITIES)`, () => {
      expect(SpawnVariation[field]).toBeInstanceOf(Float32Array);
      expect(SpawnVariation[field].length).toBe(MAX_ENTITIES);
    });
  }

  for (let slot = 0; slot < 40; slot++) {
    it(`writes variation channels on entity ${slot}`, () => {
      const r = 0.9 + slot * 0.001;
      const g = 0.85 + slot * 0.002;
      const b = 0.8 + slot * 0.003;
      const bright = 0.95 + slot * 0.0005;
      const contrast = 1.0 + slot * 0.0004;
      SpawnVariation.colorR[slot] = r;
      SpawnVariation.colorG[slot] = g;
      SpawnVariation.colorB[slot] = b;
      SpawnVariation.brightness[slot] = bright;
      SpawnVariation.contrast[slot] = contrast;
      expect(SpawnVariation.colorR[slot]).toBeCloseTo(r, 5);
      expect(SpawnVariation.colorG[slot]).toBeCloseTo(g, 5);
      expect(SpawnVariation.colorB[slot]).toBeCloseTo(b, 5);
      expect(SpawnVariation.brightness[slot]).toBeCloseTo(bright, 5);
      expect(SpawnVariation.contrast[slot]).toBeCloseTo(contrast, 5);
      for (const field of FIELDS) SpawnVariation[field][slot] = 0;
    });
  }
});
