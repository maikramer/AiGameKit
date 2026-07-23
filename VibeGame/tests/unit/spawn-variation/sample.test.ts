import { describe, expect, it } from 'bun:test';
import {
  hashWorldXZ,
  sampleVariation,
} from '../../../src/plugins/spawn-variation/sample';
import { getVariationPreset } from '../../../src/plugins/spawn-variation/presets';
import type { VariationGeometryInput } from '../../../src/plugins/spawn-variation/types';

const BASE_GEOM: VariationGeometryInput = {
  randomYaw: false,
  scaleDistribution: 'linear',
  scaleDiscreteValues: [],
  scaleMin: 1,
  scaleMax: 1,
  scaleAxisMin: 1,
  scaleAxisMax: 1,
  yawDistribution: 'linear',
  yawDiscreteDeg: [],
};

describe('hashWorldXZ', () => {
  for (let x = -5; x <= 5; x++) {
    for (let z = -5; z <= 5; z++) {
      it(`returns [0,1) at grid (${x}, ${z})`, () => {
        const h = hashWorldXZ(x * 3.7, z * 2.1);
        expect(h).toBeGreaterThanOrEqual(0);
        expect(h).toBeLessThan(1);
      });
    }
  }

  it('is stable for the same world coordinates', () => {
    expect(hashWorldXZ(12.34, 56.78)).toBe(hashWorldXZ(12.34, 56.78));
  });
});

describe('sampleVariation geometry', () => {
  let seq = 0;
  const rand = () => {
    seq = (seq + 0.173) % 1;
    return seq;
  };

  for (let i = 0; i < 30; i++) {
    it(`identity visual yields unit tint on draw ${i}`, () => {
      const sample = sampleVariation(
        BASE_GEOM,
        getVariationPreset('none'),
        rand,
        i * 4,
        i * 7
      );
      expect(sample.scaleUniform).toBe(1);
      expect(sample.colorR).toBe(1);
      expect(sample.colorG).toBe(1);
      expect(sample.colorB).toBe(1);
      expect(sample.brightness).toBe(1);
      expect(sample.contrast).toBe(1);
      expect(sample.yawRad).toBe(0);
    });
  }

  for (const preset of ['tree', 'foliage', 'rock'] as const) {
    for (let i = 0; i < 10; i++) {
      it(`${preset} visual sample ${i} keeps channels in sane ranges`, () => {
        const visual = getVariationPreset(preset);
        const sample = sampleVariation(
          {
            ...BASE_GEOM,
            randomYaw: true,
            scaleMin: 0.8,
            scaleMax: 1.2,
            scaleAxisMin: 0.9,
            scaleAxisMax: 1.1,
          },
          visual,
          () => (i * 0.13 + preset.length * 0.01) % 1,
          i * 11.3,
          i * 9.7
        );
        expect(sample.scaleUniform).toBeGreaterThanOrEqual(0.8);
        expect(sample.scaleUniform).toBeLessThanOrEqual(1.2);
        expect(sample.brightness).toBeGreaterThanOrEqual(visual.brightnessMin);
        expect(sample.brightness).toBeLessThanOrEqual(visual.brightnessMax);
        expect(sample.contrast).toBeGreaterThanOrEqual(visual.contrastMin);
        expect(sample.contrast).toBeLessThanOrEqual(visual.contrastMax);
        expect(sample.yawRad).toBeGreaterThanOrEqual(0);
        expect(sample.yawRad).toBeLessThanOrEqual(Math.PI * 2 + 1e-6);
      });
    }
  }
});

describe('sampleVariation discrete scales', () => {
  for (const value of [0.5, 1, 1.5, 2]) {
    it(`discrete scale picks one of [${value}]`, () => {
      const sample = sampleVariation(
        {
          ...BASE_GEOM,
          scaleDistribution: 'discrete',
          scaleDiscreteValues: [value],
        },
        getVariationPreset('none'),
        () => 0.5,
        0,
        0
      );
      expect(sample.scaleUniform).toBe(value);
    });
  }
});
