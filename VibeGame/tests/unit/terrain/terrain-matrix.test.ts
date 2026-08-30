import { describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  effectiveResolution,
  resolutionForLevel,
} from '../../../src/plugins/terrain/lod-select';
import {
  getTerrainContext,
  fireGroundMutationCallbacks,
  fireHeightmapReloadCallbacks,
  isTerrainDynamicsBlocking,
  registerGroundMutationCallback,
  registerHeightmapReloadCallback,
} from '../../../src/plugins/terrain/utils';
import { createFlatSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

describe('terrain matrix: getTerrainContext', () => {
  it('returns per-state map', () => {
    const a = new State();
    const b = new State();
    expect(getTerrainContext(a)).not.toBe(getTerrainContext(b));
  });
  it('same state returns same map', () => {
    const s = new State();
    expect(getTerrainContext(s)).toBe(getTerrainContext(s));
  });
});

describe('terrain matrix: isTerrainDynamicsBlocking empty', () => {
  it('no terrain entities → not blocking', () => {
    const s = new State();
    expect(isTerrainDynamicsBlocking(s)).toBe(false);
  });
});

describe('terrain matrix: callback registration', () => {
  it('heightmap reload callback fires', () => {
    const s = new State();
    let n = 0;
    registerHeightmapReloadCallback(s, () => {
      n++;
    });
    registerHeightmapReloadCallback(s, () => {
      n++;
    });
    fireHeightmapReloadCallbacks(s);
    expect(n).toBe(2);
  });
  it('ground mutation callback fires', () => {
    const s = new State();
    let hit = false;
    registerGroundMutationCallback(s, () => {
      hit = true;
    });
    fireGroundMutationCallbacks(s);
    expect(hit).toBe(true);
  });
});

describe('terrain matrix: effectiveResolution grid', () => {
  const base = 64;
  for (const level of [0, 1, 2, 3, 4, 5]) {
    for (const boost of [0, 64, 128, 255]) {
      it(`level=${level} boost=${boost} within bounds`, () => {
        const res = effectiveResolution(base, level, boost);
        expect(res).toBeGreaterThanOrEqual(resolutionForLevel(base, level));
        expect(res).toBeLessThanOrEqual(base);
      });
    }
  }
});

describe('terrain matrix: flat sampler height', () => {
  const sampler = createFlatSampler(256, 3.5);
  it('in-bounds sample uses normalized height scale', () => {
    expect(sampleHeightAt(sampler, 0, 0)).toBe(0);
  });
  it('out-of-bounds returns zero', () => {
    expect(sampleHeightAt(sampler, 200, 200)).toBe(0);
  });
  it('worldSize and maxHeight preserved on sampler', () => {
    expect(sampler.worldSize).toBe(256);
    expect(sampler.maxHeight).toBeCloseTo(3.5, 5);
  });
});

describe('terrain matrix: resolutionForLevel monotonic', () => {
  const base = 128;
  let prev = resolutionForLevel(base, 0);
  for (let level = 1; level <= 6; level++) {
    it(`level ${level} resolution <= previous`, () => {
      const cur = resolutionForLevel(base, level);
      expect(cur).toBeLessThanOrEqual(prev);
      prev = cur;
    });
  }
});
