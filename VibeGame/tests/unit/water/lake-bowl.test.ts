import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { LakeBowl } from '../../../src/plugins/water/lake-bowl';

function flatSampler(heightNorm = 0.5, size = 64): HeightSampler {
  const data = new Float32Array(size * size).fill(heightNorm);
  return { width: size, height: size, data, worldSize: 100, maxHeight: 100 };
}

describe('LakeBowl', () => {
  it('computeAabb covers the disc (× margin)', () => {
    const bowl = new LakeBowl({
      localX: 20,
      localZ: 0,
      worldX: 20,
      worldZ: 0,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    const aabb = bowl.computeAabb();
    // The AABB must contain the disc centred at (20,0) radius 6 × margin.
    expect(aabb.minX).toBeLessThanOrEqual(20 - 6);
    expect(aabb.maxX).toBeGreaterThanOrEqual(20 + 6);
    expect(aabb.minZ).toBeLessThanOrEqual(0 - 6);
    expect(aabb.maxZ).toBeGreaterThanOrEqual(0 + 6);
  });

  it('carve lowers the sampler at the lake centre and returns carved=true', () => {
    const sampler = flatSampler(0.5);
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      worldX: 0,
      worldZ: 0,
      radius: 10,
      depth: 8,
      waterOffset: 0.3,
    });
    const before = sampleHeightAt(sampler, 0, 0);
    const result = bowl.carve(sampler);
    const after = sampleHeightAt(sampler, 0, 0);
    expect(result.carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('carve returns carved=false on a dataless sampler', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 100,
      maxHeight: 100,
    };
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      worldX: 0,
      worldZ: 0,
      radius: 10,
      depth: 8,
      waterOffset: 0.3,
    });
    expect(bowl.carve(flat).carved).toBe(false);
  });

  it('worldOrigin returns the world centre for mesh placement', () => {
    const bowl = new LakeBowl({
      localX: 5,
      localZ: 7,
      worldX: 105,
      worldZ: 107,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    expect(bowl.worldOrigin()).toEqual({ x: 105, z: 107 });
  });

  it('densityBoost returns 255', () => {
    const bowl = new LakeBowl({
      localX: 0,
      localZ: 0,
      worldX: 0,
      worldZ: 0,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    expect(bowl.densityBoost()).toBe(255);
  });

  it('toWaterBody(worldWaterY) returns a lake body with kind="lake" and the given waterY', () => {
    const bowl = new LakeBowl({
      localX: 5,
      localZ: 7,
      worldX: 5,
      worldZ: 7,
      radius: 6,
      depth: 2,
      waterOffset: 0.3,
    });
    const body = bowl.toWaterBody(42);
    expect(body.kind).toBe('lake');
    if (body.kind === 'lake') {
      expect(body.x).toBe(5);
      expect(body.z).toBe(7);
      expect(body.radius).toBe(6);
      expect(body.waterY).toBe(42);
    }
  });
});
