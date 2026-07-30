import { describe, expect, it } from 'bun:test';
import {
  applyCorridorDensity,
  applyFeatureDensity,
  densityLeafPad,
} from '../../../src/plugins/terrain/ground-mutation';
import {
  boostAt,
  buildDensityMap,
} from '../../../src/plugins/terrain/density-map';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  corridorAabb,
  nearestOnPolyline,
  segmentAabb,
} from '../../../src/plugins/terrain/corridor';
import {
  forEachTexelInAabb,
  texelIndexRange,
} from '../../../src/plugins/terrain/height-brush';

function flatSampler(fill = 0.5, size = 33, world = 32): HeightSampler {
  return {
    data: new Float32Array(size * size).fill(fill),
    width: size,
    height: size,
    worldSize: world,
    maxHeight: 100,
  } as HeightSampler;
}

describe('corridor: nearestOnPolyline / aabb', () => {
  it('finds the closest point on a straight segment', () => {
    const path = [-10, 0, 10, 0];
    const n = nearestOnPolyline(path, 0, 3);
    expect(n).not.toBeNull();
    expect(n!.dist).toBeCloseTo(3);
    expect(n!.seg).toBe(0);
    expect(n!.t).toBeCloseTo(0.5);
    expect(n!.cx).toBeCloseTo(0);
    expect(n!.cz).toBeCloseTo(0);
  });

  it('corridorAabb expands by pad', () => {
    const aabb = corridorAabb([0, 0, 10, 0], 2);
    expect(aabb).toEqual({ minX: -2, maxX: 12, minZ: -2, maxZ: 2 });
  });

  it('segmentAabb covers both endpoints + reach', () => {
    expect(segmentAabb(0, 0, 4, 0, 1)).toEqual({
      minX: -1,
      maxX: 5,
      minZ: -1,
      maxZ: 1,
    });
  });
});

describe('height-brush: forEachTexelInAabb / texelIndexRange', () => {
  it('expands AABB by 1 texel (same contract as applyHeightBrush)', () => {
    const s = flatSampler(0.5, 33, 32); // texel 1 m
    const range = texelIndexRange(s, {
      minX: 0.4,
      maxX: 0.6,
      minZ: 0.4,
      maxZ: 0.6,
    });
    expect(range).not.toBeNull();
    // Without ±1 the range would be a single texel; with margin it widens.
    expect(range!.x1 - range!.x0).toBeGreaterThanOrEqual(2);
    expect(range!.z1 - range!.z0).toBeGreaterThanOrEqual(2);
  });

  it('visits texel centres inside the AABB', () => {
    const s = flatSampler();
    const visited: Array<[number, number]> = [];
    forEachTexelInAabb(
      s,
      { minX: -1, maxX: 1, minZ: -1, maxZ: 1 },
      (_idx, wx, wz) => {
        visited.push([wx, wz]);
      }
    );
    expect(visited.length).toBeGreaterThan(0);
    for (const [wx, wz] of visited) {
      expect(Math.abs(wx)).toBeLessThanOrEqual(3); // +1 texel margin
      expect(Math.abs(wz)).toBeLessThanOrEqual(3);
    }
  });
});

describe('ground-mutation: density helpers', () => {
  it('densityLeafPad = half deepest leaf', () => {
    // world 2000, levels 6 → deepest leaf = 2000/32 = 62.5 → half = 31.25
    expect(densityLeafPad(2000, 6)).toBeCloseTo(31.25);
  });

  it('applyCorridorDensity stamps boost along the path', () => {
    const sampler = flatSampler(0.5, 65, 200);
    const density = buildDensityMap(sampler, 20);
    applyCorridorDensity(density, [-40, 0, 40, 0], 8, 255, 0);
    expect(boostAt(density, 0, 0)).toBe(255);
    expect(boostAt(density, 0, 40)).toBe(0);
  });

  it('applyFeatureDensity with leafPad expands the AABB', () => {
    const sampler = flatSampler(0.5, 65, 200);
    const density = buildDensityMap(sampler, 20);
    // Core AABB is tiny; leafPad 20 should reach a nearby tile.
    applyFeatureDensity(
      density,
      { minX: -2, maxX: 2, minZ: -2, maxZ: 2 },
      200,
      20
    );
    expect(boostAt(density, 15, 0)).toBe(200);
  });
});
