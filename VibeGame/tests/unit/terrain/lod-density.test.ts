import { describe, expect, it } from 'bun:test';
import {
  effectiveResolution,
  resolutionForLevel,
  selectChunks,
} from '../../../src/plugins/terrain/lod-select';
import {
  applyOverride,
  buildDensityMap,
  maxBoostOverAabb,
} from '../../../src/plugins/terrain/density-map';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

function flatSampler(): HeightSampler {
  return {
    width: 2,
    height: 2,
    data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
    worldSize: 100,
    maxHeight: 10,
  };
}

describe('LOD + density integration', () => {
  it('a chunk overlapping a max-boost region resolves to higher resolution than camera-LOD alone', () => {
    const base = 64;
    const sampler = flatSampler();
    const density = buildDensityMap(sampler, 8);
    // Mark a region around the world origin as maximally important (simulates
    // a <Lake> override applied before carve).
    applyOverride(density, { minX: -10, minZ: -10, maxX: 10, maxZ: 10 }, 255);

    const worldSize = sampler.worldSize;
    const level = 5;
    const chunkSize = worldSize / Math.pow(2, level);
    const chunks = selectChunks(worldSize, 6, 2.0, 1.2, 0, 0);
    const desc = chunks.find((d) => d.level === level);
    expect(desc).toBeDefined();
    const d = desc!;
    const aabb = {
      minX: d.originX - chunkSize / 2,
      minZ: d.originZ - chunkSize / 2,
      maxX: d.originX + chunkSize / 2,
      maxZ: d.originZ + chunkSize / 2,
    };
    const boost = maxBoostOverAabb(density, aabb);
    const lodRes = resolutionForLevel(base, level);
    const effRes = effectiveResolution(base, level, boost);

    // If the origin chunk actually overlaps the boosted region, it must be
    // denser than camera-LOD alone; and it must never exceed base.
    if (boost > 0) {
      expect(effRes).toBeGreaterThan(lodRes);
    }
    expect(effRes).toBeLessThanOrEqual(base);
  });

  it('without a density boost, effective resolution equals camera-LOD resolution', () => {
    const base = 64;
    const sampler = flatSampler();
    const density = buildDensityMap(sampler, 8); // flat → all zeros
    const aabb = { minX: -5, minZ: -5, maxX: 5, maxZ: 5 };
    const boost = maxBoostOverAabb(density, aabb);
    expect(boost).toBe(0);
    for (const level of [0, 1, 2, 3, 4, 5]) {
      expect(effectiveResolution(base, level, boost)).toBe(
        resolutionForLevel(base, level)
      );
    }
  });

  it('density boost forces deepest leaves even when camera is far', () => {
    const worldSize = 256;
    const levels = 6;
    const base = 64;
    const density = buildDensityMap(
      {
        width: 2,
        height: 2,
        data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
        worldSize,
        maxHeight: 10,
      },
      16
    );
    // Corridor stamp across the origin — same contract as road flatten.
    applyOverride(density, { minX: -8, minZ: -8, maxX: 8, maxZ: 8 }, 255);

    // Camera far away so camera-LOD alone would keep a single root leaf.
    // selectChunks returns a scratch buffer — snapshot before the next call.
    const far = selectChunks(worldSize, levels, 2.0, 1.2, 5000, 5000).map(
      (c) => ({ ...c })
    );
    expect(far).toHaveLength(1);
    expect(far[0]!.level).toBe(0);

    const forced = selectChunks(
      worldSize,
      levels,
      2.0,
      1.2,
      5000,
      5000,
      density,
      base
    );
    expect(Math.max(...forced.map((c) => c.level))).toBe(levels - 1);
    expect(forced.length).toBeGreaterThan(far.length);

    // Boosted region is covered by deepest leaves.
    const originLeaves = forced.filter(
      (c) =>
        c.level === levels - 1 &&
        Math.abs(c.originX) < 20 &&
        Math.abs(c.originZ) < 20
    );
    expect(originLeaves.length).toBeGreaterThan(0);
  });
});
