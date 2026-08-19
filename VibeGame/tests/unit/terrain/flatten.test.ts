import { describe, expect, it } from 'bun:test';
import { flattenRect } from '../../../src/plugins/terrain/flatten';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

function rampSampler(size = 64, world = 200): HeightSampler {
  // Height rises linearly west→east: 0 at -world/2, 50 m at +world/2.
  const data = new Float32Array(size * size);
  for (let z = 0; z < size; z++) {
    for (let x = 0; x < size; x++) {
      data[z * size + x] = (x / (size - 1)) * 0.5; // maxHeight 100 → 0..50 m
    }
  }
  return { width: size, height: size, data, worldSize: world, maxHeight: 100 };
}

describe('flattenRect', () => {
  it('levels the core to the target height (both raising and lowering)', () => {
    const s = rampSampler();
    const changed = flattenRect(s, {
      centerX: 0,
      centerZ: 0,
      halfX: 20,
      halfZ: 20,
      targetY: 25,
      falloff: 10,
      cornerRadius: 5,
    });
    expect(changed).toBe(true);
    // West half of the core was below 25 (raised), east half above (lowered).
    expect(sampleHeightAt(s, -15, 0)).toBeCloseTo(25, 0);
    expect(sampleHeightAt(s, 15, 0)).toBeCloseTo(25, 0);
    expect(sampleHeightAt(s, 0, 15)).toBeCloseTo(25, 0);
  });

  it('leaves terrain beyond the falloff untouched', () => {
    const s = rampSampler();
    const before = sampleHeightAt(s, 60, 0);
    flattenRect(s, {
      centerX: 0,
      centerZ: 0,
      halfX: 20,
      halfZ: 20,
      targetY: 25,
      falloff: 10,
      cornerRadius: 5,
    });
    expect(sampleHeightAt(s, 60, 0)).toBeCloseTo(before, 5);
  });

  it('blends smoothly across the falloff ring', () => {
    const s = rampSampler();
    flattenRect(s, {
      centerX: 0,
      centerZ: 0,
      halfX: 20,
      halfZ: 20,
      targetY: 25,
      falloff: 10,
      cornerRadius: 5,
    });
    // In the ring (edge at x=20, falloff to 30) height sits between the pad
    // level and the original ramp.
    const mid = sampleHeightAt(s, 25, 0);
    const original = (((25 + 100) / 200) * 100) / 2; // ramp: 31.25 m at x=25
    expect(mid).toBeGreaterThan(25);
    expect(mid).toBeLessThan(original + 0.5);
  });

  it('no-ops on a dataless sampler', () => {
    const s: HeightSampler = {
      width: 2,
      height: 2,
      data: null,
      worldSize: 100,
      maxHeight: 50,
    };
    expect(
      flattenRect(s, {
        centerX: 0,
        centerZ: 0,
        halfX: 10,
        halfZ: 10,
        targetY: 5,
        falloff: 5,
        cornerRadius: 2,
      })
    ).toBe(false);
  });

  it('keeps the reconstructed core on the pad plane on a coarse hillside sampler', () => {
    // Texel step (200/63 ≈ 3.2 m) against a 50 m falloff-side rise: without
    // the cell guard, the first ring texel above the plane lifts the bilinear
    // reconstruction over the core edge (a one-texel lip for props/roads).
    const s = rampSampler(65, 200);
    flattenRect(s, {
      centerX: 0,
      centerZ: 0,
      halfX: 20,
      halfZ: 20,
      targetY: 25,
      falloff: 10,
      cornerRadius: 5,
    });
    // Core interior reads as the plane; on the cut side (east, ramp above
    // the plane) the guard keeps the edge ring from lifting the
    // reconstruction over the plane. The fill side (west) keeps its blended
    // approach — the guard only ever lowers.
    for (let x = 3; x <= 19; x += 4) {
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(25.1);
      expect(sampleHeightAt(s, x, 0)).toBeGreaterThan(24.9);
    }
    for (let x = -19; x <= -3; x += 4) {
      expect(sampleHeightAt(s, x, 0)).toBeGreaterThan(24.4);
      expect(sampleHeightAt(s, x, 0)).toBeLessThan(25.1);
    }
  });
});
