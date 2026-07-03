import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  applyOverride,
  boostAt,
  buildDensityMap,
  maxBoostOverAabb,
  type DensityMap,
  type WorldAabb,
} from '../../../src/plugins/terrain/density-map';

/** Build a sampler where each texel's normalized height comes from `paint(wx,wz)`. */
function syntheticSampler(
  size: number,
  worldSize: number,
  maxHeight: number,
  paint: (x: number, z: number) => number
): HeightSampler {
  const data = new Float32Array(size * size);
  const half = worldSize / 2;
  const step = worldSize / (size - 1);
  for (let z = 0; z < size; z++) {
    for (let x = 0; x < size; x++) {
      data[z * size + x] = paint(x * step - half, z * step - half);
    }
  }
  return { width: size, height: size, data, worldSize, maxHeight };
}

describe('buildDensityMap', () => {
  it('returns zero boost on a perfectly flat field', () => {
    const sampler = syntheticSampler(16, 100, 10, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    expect(dm.tilesX).toBe(8);
    expect(dm.tilesZ).toBe(8);
    expect(dm.boost.every((b) => b === 0)).toBe(true);
  });

  it('returns an all-zero map for a dataless (flat) sampler', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 100,
      maxHeight: 10,
    };
    const dm = buildDensityMap(flat, 8);
    expect(dm.boost.length).toBe(64);
    expect(dm.boost.every((b) => b === 0)).toBe(true);
  });

  it('assigns higher boost to a tile straddling a height step than to a far flat tile', () => {
    // Left half flat at 0, right half flat at 1 → a step at x=0.
    const sampler = syntheticSampler(32, 100, 10, (x) => (x < 0 ? 0 : 1));
    const dm = buildDensityMap(sampler, 8);
    // x=0 maps to tile 4 of 8 over [-50,50] (floor((0+50)/100*8)=4); the step
    // straddles that boundary so tile 4 carries the high-variance probes.
    const mid = Math.floor(dm.tilesZ / 2) * dm.tilesX;
    const stepBoost = dm.boost[mid + 4];
    const flatBoost = dm.boost[mid + 0];
    expect(stepBoost).toBeGreaterThan(flatBoost);
    expect(stepBoost).toBe(255);
  });

  it('respects a higher threshold to suppress low-variance tiles', () => {
    // Gentle bump well below a high threshold → no boost.
    const sampler = syntheticSampler(
      16,
      100,
      10,
      (x, z) => 0.5 + 0.001 * Math.hypot(x, z)
    );
    const dm = buildDensityMap(sampler, 8, { threshold: 0.5 });
    expect(dm.boost.every((b) => b === 0)).toBe(true);
  });
});

describe('applyOverride', () => {
  it('sets boost to the given value within the AABB and clamps to 255', () => {
    const sampler = syntheticSampler(16, 100, 10, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    applyOverride(dm, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 255);
    expect(boostAt(dm, 0, 0)).toBe(255);
    // A point far outside the AABB stays at its original value (0 here).
    expect(boostAt(dm, 40, 40)).toBe(0);
  });

  it('clamps negative boost to 0 and never lowers existing values', () => {
    const dm: DensityMap = {
      tilesX: 4,
      tilesZ: 4,
      boost: new Uint8Array(16).fill(100),
      worldSize: 100,
    };
    applyOverride(dm, { minX: -50, minZ: -50, maxX: 50, maxZ: 50 }, -50);
    // -50 clamps to 0, which is below existing 100 → no change.
    expect(dm.boost.every((b) => b === 100)).toBe(true);
  });

  it('takes the max with an existing higher boost (composes overrides)', () => {
    const sampler = syntheticSampler(16, 100, 10, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    applyOverride(dm, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 200);
    applyOverride(dm, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 100);
    expect(boostAt(dm, 0, 0)).toBe(200);
  });
});

describe('maxBoostOverAabb', () => {
  it('returns the maximum boost among tiles intersecting the chunk AABB', () => {
    const dm: DensityMap = {
      tilesX: 8,
      tilesZ: 8,
      boost: new Uint8Array(64),
      worldSize: 100,
    };
    dm.boost[0] = 10;
    dm.boost[63] = 250;
    // AABB covering only the bottom-left tile.
    expect(
      maxBoostOverAabb(dm, { minX: -50, minZ: -50, maxX: -40, maxZ: -40 })
    ).toBe(10);
    // AABB covering the whole world.
    expect(
      maxBoostOverAabb(dm, { minX: -50, minZ: -50, maxX: 50, maxZ: 50 })
    ).toBe(250);
  });

  it('returns 0 for an AABB over an entirely flat region', () => {
    const sampler = syntheticSampler(16, 100, 10, () => 0.5);
    const dm = buildDensityMap(sampler, 8);
    expect(
      maxBoostOverAabb(dm, { minX: -20, minZ: -20, maxX: 20, maxZ: 20 })
    ).toBe(0);
  });
});

describe('boostAt', () => {
  it('clamps out-of-world coordinates to the nearest edge tile', () => {
    const dm: DensityMap = {
      tilesX: 4,
      tilesZ: 4,
      boost: new Uint8Array(16).fill(7),
      worldSize: 100,
    };
    expect(boostAt(dm, 999, 999)).toBe(7);
    expect(boostAt(dm, -999, -999)).toBe(7);
  });
});

// Silence the unused-import lint for the type re-export above in strict setups.
export type { WorldAabb };
