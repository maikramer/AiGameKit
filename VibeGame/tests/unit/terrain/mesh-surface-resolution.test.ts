import { describe, expect, it } from 'bun:test';
import {
  applyOverride,
  buildDensityMap,
} from '../../../src/plugins/terrain/density-map';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import {
  effectiveResolution,
  meshSurfaceResolutionForPoint,
} from '../../../src/plugins/terrain/lod-select';
import { sampleMeshSurfaceHeight } from '../../../src/plugins/spawner/surface';

function flatSampler(worldSize = 2000): HeightSampler {
  return {
    width: 2,
    height: 2,
    data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
    worldSize,
    maxHeight: 100,
  };
}

/** Coarse lattice misses a carved dip; fine lattice (density-boosted leaf) sees it. */
function carvedDipSampler(): HeightSampler {
  // Heightmap fine enough that a ~10 m dip is representable; base mesh
  // lattice (res=64 → 31.25 m) still bridges the plateau across it.
  const width = 257;
  const height = 257;
  const data = new Float32Array(width * height);
  // Midway between base-res=64 lattice vertices (local x = 15.625).
  const dipX = 15.625;
  for (let z = 0; z < height; z++) {
    for (let x = 0; x < width; x++) {
      const lx = (x / (width - 1) - 0.5) * 2000;
      data[z * width + x] = Math.abs(lx - dipX) < 5 ? 0.1 : 0.5;
    }
  }
  return { width, height, data, worldSize: 2000, maxHeight: 100 };
}

describe('meshSurfaceResolutionForPoint', () => {
  it('returns base resolution when density is missing or boost is 0', () => {
    const density = buildDensityMap(flatSampler(), 8);
    expect(meshSurfaceResolutionForPoint(64, 4, undefined, 0, 0)).toBe(64);
    expect(meshSurfaceResolutionForPoint(64, 4, density, 0, 0)).toBe(64);
  });

  it('raises resolution to match leaf-chunk lattice under max boost', () => {
    const sampler = flatSampler();
    const density = buildDensityMap(sampler, 8);
    applyOverride(density, { minX: -20, minZ: -20, maxX: 20, maxZ: 20 }, 255);

    const base = 64;
    const levels = 4;
    const res = meshSurfaceResolutionForPoint(base, levels, density, 0, 0);
    const leafRes = effectiveResolution(base, levels, 255);
    expect(res).toBe(leafRes * 2 ** levels);
    expect(res).toBeGreaterThan(base);
  });

  it('anchors spawn height to the carved dip when boost is active (no float)', () => {
    const sampler = carvedDipSampler();
    const density = buildDensityMap(sampler, 64);
    const dipX = 15.625;
    applyOverride(
      density,
      { minX: dipX - 20, minZ: -20, maxX: dipX + 20, maxZ: 20 },
      255
    );

    const base = 64;
    const levels = 4;
    const coarse = sampleMeshSurfaceHeight(sampler, dipX, 0, base);
    const fineRes = meshSurfaceResolutionForPoint(
      base,
      levels,
      density,
      dipX,
      0
    );
    const fine = sampleMeshSurfaceHeight(sampler, dipX, 0, fineRes);
    const analytic = sampleHeightAt(sampler, dipX, 0);

    // Coarse 31 m lattice bridges the plateau across the dip → too high.
    expect(coarse).toBeGreaterThan(analytic + 5);
    // Density-aware lattice tracks the visible carve surface.
    expect(fine).toBeLessThan(coarse - 5);
    expect(fine).toBeCloseTo(analytic, 0);
  });
});
