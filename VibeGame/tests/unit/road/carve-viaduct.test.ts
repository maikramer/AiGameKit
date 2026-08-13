import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import {
  carveRoadCorridor,
  groundedPathRuns,
  flyingPathRuns,
  viaductMask,
} from '../../../src/plugins/road/carve';

/** Sampler with a rectangular pit — the valley a viaduct flies over. */
function valleySampler(
  plateauY: number,
  valleyY: number,
  minX: number,
  maxX: number,
  size = 512,
  world = 800,
  maxHeight = 100
): HeightSampler {
  const data = new Float32Array(size * size).fill(plateauY / maxHeight);
  const step = world / (size - 1);
  const half = world / 2;
  for (let zi = 0; zi < size; zi++) {
    for (let xi = 0; xi < size; xi++) {
      const wx = xi * step - half;
      if (wx >= minX && wx <= maxX) {
        data[zi * size + xi] = valleyY / maxHeight;
      }
    }
  }
  return { width: size, height: size, data, worldSize: world, maxHeight };
}

describe('viaductMask', () => {
  const arcs = [0, 100, 200, 300, 400, 500];

  it('is 1 where the bed sits on the ground', () => {
    const design = [10, 10, 10, 10, 10, 10];
    const natural = [10, 9, 11, 10, 8, 10];
    expect(viaductMask(arcs, design, natural, 6)).toEqual([1, 1, 1, 1, 1, 1]);
  });

  it('is 0 in the middle of a span and ramps at the abutments', () => {
    const design = [10, 10, 30, 30, 10, 10];
    const natural = [10, 10, 2, 2, 10, 10];
    const mask = viaductMask(arcs, design, natural, 6, 100);
    expect(mask[0]).toBe(1);
    expect(mask[1]).toBe(1);
    // 100 m from the nearest grounded station with a 100 m ramp = fully flying.
    expect(mask[2]).toBe(0);
    expect(mask[3]).toBe(0);
    expect(mask[4]).toBe(1);
  });

  it('eases the carve out over the ramp length', () => {
    const design = [10, 30, 30, 30, 10, 10];
    const natural = [10, 2, 2, 2, 10, 10];
    const mask = viaductMask(arcs, design, natural, 6, 200);
    // Station 1 is 100 m from grounded station 0 → half weight.
    expect(mask[1]).toBeCloseTo(0.5, 6);
    expect(mask[2]).toBe(0);
  });

  it('treats a cutting as grounded', () => {
    const design = [10, 10, 2, 2, 10, 10];
    const natural = [10, 10, 20, 20, 10, 10];
    expect(viaductMask(arcs, design, natural, 6)).toEqual([1, 1, 1, 1, 1, 1]);
  });
});

describe('groundedPathRuns', () => {
  const path = [0, 0, 10, 0, 20, 0, 30, 0, 40, 0, 50, 0];

  it('splits the path at the spans', () => {
    const runs = groundedPathRuns(path, [1, 1, 0, 0, 1, 1]);
    expect(runs).toEqual([
      [0, 0, 10, 0],
      [40, 0, 50, 0],
    ]);
  });

  it('returns the whole path when nothing flies', () => {
    expect(groundedPathRuns(path, [1, 1, 1, 1, 1, 1])).toEqual([path]);
  });

  it('returns nothing when the whole corridor flies', () => {
    expect(groundedPathRuns(path, [0, 0, 0, 0, 0, 0])).toEqual([]);
  });

  it('drops single-node runs that cannot form a segment', () => {
    expect(groundedPathRuns(path, [1, 0, 0, 0, 0, 0])).toEqual([]);
  });
});

describe('flyingPathRuns', () => {
  const path = [0, 0, 10, 0, 20, 0, 30, 0, 40, 0, 50, 0];
  const heights = [1, 2, 18, 19, 3, 4];

  it('is the inverse of groundedPathRuns and keeps deck Y in lockstep', () => {
    const mask = [1, 1, 0, 0, 1, 1];
    const runs = flyingPathRuns(path, mask, heights);
    expect(runs).toEqual([{ path: [20, 0, 30, 0], pathY: [18, 19] }]);
  });

  it('returns the whole path when everything flies', () => {
    const runs = flyingPathRuns(path, [0, 0, 0, 0, 0, 0], heights);
    expect(runs).toHaveLength(1);
    expect(runs[0]!.path).toEqual(path);
    expect(runs[0]!.pathY).toEqual(heights);
  });

  it('returns nothing when nothing flies', () => {
    expect(flyingPathRuns(path, [1, 1, 1, 1, 1, 1], heights)).toEqual([]);
  });
});

describe('carveRoadCorridor — viaduct clearance', () => {
  // Straight along +X at z=0; the valley is the strip -100 < x < 100.
  const PATH = [-300, 0, -150, 0, 0, 0, 150, 0, 300, 0];
  // Deck stays at 30 m across the valley, ramps down to the plateau (10 m).
  const PROFILE = [10, 12, 30, 12, 10];

  const carve = (extra: Record<string, unknown> = {}) => {
    const s = valleySampler(10, 2, -100, 100);
    carveRoadCorridor(s, {
      path: PATH,
      width: 20,
      falloff: 8,
      window: 60,
      profileY: PROFILE,
      viaductClearance: 6,
      viaductRamp: 40,
      ...extra,
    });
    return s;
  };

  it('leaves the valley floor exactly as it was', () => {
    const s = carve();
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(2, 1);
    expect(sampleHeightAt(s, -40, 0)).toBeCloseTo(2, 1);
  });

  it('still grades the approaches on solid ground', () => {
    const s = carve();
    expect(sampleHeightAt(s, -300, 0)).toBeCloseTo(10, 0);
    expect(sampleHeightAt(s, 300, 0)).toBeCloseTo(10, 0);
  });

  it('fills the valley when the clearance is not set', () => {
    const s = carve({ viaductClearance: undefined });
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(30, 0);
  });

  it('reports the carve mask to the caller', () => {
    const seen: number[][] = [];
    const s = valleySampler(10, 2, -100, 100);
    carveRoadCorridor(s, {
      path: PATH,
      width: 20,
      falloff: 8,
      window: 60,
      profileY: PROFILE,
      viaductClearance: 6,
      onGroundMask: (m) => {
        seen.push(m);
      },
    });
    const mask = seen[0]!;
    expect(mask.length).toBe(PATH.length / 2);
    expect(mask[0]).toBe(1);
    expect(mask[2]).toBeLessThan(1);
  });

  it('reports an all-grounded mask when no clearance is configured', () => {
    const seen: number[][] = [];
    const s = valleySampler(10, 2, -100, 100);
    carveRoadCorridor(s, {
      path: PATH,
      width: 20,
      falloff: 8,
      window: 60,
      profileY: PROFILE,
      onGroundMask: (m) => {
        seen.push(m);
      },
    });
    expect(seen[0]).toEqual([1, 1, 1, 1, 1]);
  });

  it('needs a design elevation — a surveyed terrace never flies', () => {
    const seen: number[][] = [];
    const s = valleySampler(10, 2, -100, 100);
    carveRoadCorridor(s, {
      path: PATH,
      width: 20,
      falloff: 8,
      window: 60,
      viaductClearance: 6,
      onGroundMask: (m) => {
        seen.push(m);
      },
    });
    expect(seen[0]).toEqual([1, 1, 1, 1, 1]);
  });
});
