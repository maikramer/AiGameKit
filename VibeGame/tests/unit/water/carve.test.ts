import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import { carveBowl, rimHeight } from '../../../src/plugins/water/carve';
import {
  isPointInWater,
  registerWaterBody,
  unregisterWaterBody,
  waterLevelAt,
} from '../../../src/plugins/water/registry';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function flatSampler(
  heightNorm: number,
  size = 64,
  world = 200
): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(heightNorm),
    worldSize: world,
    maxHeight: 100,
  };
}

describe('carveBowl', () => {
  it('carves a bowl of the requested depth at the centre', () => {
    const s = flatSampler(0.5); // terrain at 50 m
    const rim = rimHeight(s, 0, 0, 10);
    expect(rim).toBeCloseTo(50, 3);

    expect(carveBowl(s, 0, 0, 10, rim, 3)).toBe(true);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(47, 0.5);
    // rim stays at the original terrain level
    expect(sampleHeightAt(s, 15, 0)).toBeCloseTo(50, 3);
  });

  it('only ever lowers terrain (existing valleys are kept)', () => {
    const s = flatSampler(0.2); // terrain at 20 m — below the bowl profile
    carveBowl(s, 0, 0, 10, 50, 3);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(20, 3);
  });

  it('is monotonic from centre to rim', () => {
    const s = flatSampler(0.5);
    carveBowl(s, 0, 0, 12, 50, 4);
    let prev = -Infinity;
    for (let r = 0; r <= 12; r += 2) {
      const h = sampleHeightAt(s, r, 0);
      expect(h).toBeGreaterThanOrEqual(prev - 1e-6);
      prev = h;
    }
  });

  it('no-ops on a flat (dataless) sampler', () => {
    const s: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 200,
      maxHeight: 100,
    };
    expect(carveBowl(s, 0, 0, 10, 50, 3)).toBe(false);
  });
});

describe('water registry', () => {
  it('point membership + level lookup + unregister', () => {
    const state = new State();
    const body = { x: 10, z: -5, radius: 6, waterY: 42 };
    registerWaterBody(state, body);

    expect(isPointInWater(state, 10, -5)).toBe(true);
    expect(isPointInWater(state, 15, -5)).toBe(true);
    expect(isPointInWater(state, 17, -5)).toBe(false);
    expect(waterLevelAt(state, 12, -5)).toBe(42);
    expect(waterLevelAt(state, 30, 30)).toBeNull();

    unregisterWaterBody(state, body);
    expect(isPointInWater(state, 10, -5)).toBe(false);
  });
});
