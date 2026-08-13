import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { revertHeightBrush } from '../../../src/plugins/terrain/height-brush';
import {
  carveRoadCorridor,
  designRoadProfile,
  limitProfileGrade,
  smoothProfile,
} from '../../../src/plugins/road/carve';
import { pathArcs } from '../../../src/plugins/terrain/corridor';

/** Flat sampler at a constant normalized height. */
function flatSampler(
  heightNorm: number,
  size = 512,
  world = 400,
  maxHeight = 100
): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(heightNorm),
    worldSize: world,
    maxHeight,
  };
}

/** Rolling hills so a survey carve has something to terrace. */
function hillySampler(size = 512, world = 400): HeightSampler {
  const s = flatSampler(0.3, size, world);
  const step = world / (size - 1);
  const half = world / 2;
  for (let zi = 0; zi < size; zi++) {
    const wz = zi * step - half;
    for (let xi = 0; xi < size; xi++) {
      const wx = xi * step - half;
      s.data![zi * size + xi] =
        0.3 + 0.08 * Math.sin(wx / 37) + 0.05 * Math.cos(wz / 23);
    }
  }
  return s;
}

/** Straight along +X at z=0. */
const STRAIGHT = [-150, 0, -50, 0, 50, 0, 150, 0];

describe('carveRoadCorridor — authored elevation profile', () => {
  it('puts the bed exactly at the authored height, not at the terrain', () => {
    const s = flatSampler(0.3); // 30 m of ground
    const ok = carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      profileY: [10, 12, 14, 16],
    });
    expect(ok).toBe(true);
    expect(sampleHeightAt(s, -150, 0)).toBeCloseTo(10, 1);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(13, 1);
    expect(sampleHeightAt(s, 150, 0)).toBeCloseTo(16, 1);
  });

  it('does not apply platformSink to an authored profile', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      platformSink: 5,
      profileY: [10, 10, 10, 10],
    });
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(10, 1);
  });

  it('is exactly repeatable — a second carve lands on the same terrain', () => {
    const s = flatSampler(0.3);
    // The bed is a pure function of the authored profile; the falloff band is
    // a lerp toward it, so only the journal makes the whole stamp repeatable.
    const opts = {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      profileY: [10, 12, 14, 16],
      owner: 'road:repeat',
    };
    carveRoadCorridor(s, opts);
    const first = Float32Array.from(s.data!);
    carveRoadCorridor(s, opts);
    expect(Array.from(s.data!)).toEqual(Array.from(first));
  });

  it('keeps the full-weight bed put even without the journal', () => {
    const s = flatSampler(0.3);
    const opts = {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      profileY: [10, 12, 14, 16],
    };
    carveRoadCorridor(s, opts);
    carveRoadCorridor(s, opts);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(13, 1);
  });

  it('ignores a profile shorter than the path', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      profileY: [10, 12],
    });
    // Fell back to the survey: flat ground stays at 30 m minus the sink.
    expect(sampleHeightAt(s, 0, 0)).toBeGreaterThan(25);
  });
});

describe('carveRoadCorridor — per-node widths', () => {
  it('carves a wide bed where the corridor is wide and a narrow one where it is not', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 10,
      falloff: 4,
      window: 40,
      widths: [60, 60, 10, 10],
      profileY: [10, 10, 10, 10],
    });
    // Wide end: 25 m off-centre is still full-weight bed.
    expect(sampleHeightAt(s, -150, 25)).toBeCloseTo(10, 0);
    // Narrow end: the same offset is untouched natural ground.
    expect(sampleHeightAt(s, 150, 25)).toBeCloseTo(30, 0);
  });
});

describe('carveRoadCorridor — banking', () => {
  const banked = (bankRad: number) => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 30,
      falloff: 4,
      window: 40,
      profileY: [10, 10, 10, 10],
      banks: [bankRad, bankRad, bankRad, bankRad],
    });
    return s;
  };

  it('raises the driver-right side for a positive bank', () => {
    // Travelling +X the engine's right is -Z (TrackSpline convention).
    const s = banked(0.2);
    const right = sampleHeightAt(s, 0, -12);
    const left = sampleHeightAt(s, 0, 12);
    expect(right).toBeGreaterThan(10);
    expect(left).toBeLessThan(10);
    expect(right - 10).toBeCloseTo(12 * Math.sin(0.2), 1);
  });

  it('mirrors for a negative bank', () => {
    const s = banked(-0.2);
    expect(sampleHeightAt(s, 0, -12)).toBeLessThan(10);
    expect(sampleHeightAt(s, 0, 12)).toBeGreaterThan(10);
  });

  it('clamps to maxBank', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 30,
      falloff: 4,
      window: 40,
      profileY: [10, 10, 10, 10],
      banks: [1.4, 1.4, 1.4, 1.4],
      maxBank: 0.1,
    });
    expect(sampleHeightAt(s, 0, -12) - 10).toBeCloseTo(12 * Math.sin(0.1), 1);
  });

  it('holds the run-off flat at the bed edge instead of tilting it away', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      shoulderWidth: 20,
      profileY: [10, 10, 10, 10],
      banks: [0.2, 0.2, 0.2, 0.2],
    });
    const edge = 10 + 10 * Math.sin(0.2); // bed half-width = 10 m
    expect(sampleHeightAt(s, 0, -10)).toBeCloseTo(edge, 0);
    // 18 m further out into the gravel: same height, not 18 m higher.
    expect(sampleHeightAt(s, 0, -28)).toBeCloseTo(edge, 0);
  });

  it('leaves the bed level when no banks are supplied', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 30,
      falloff: 4,
      window: 40,
      profileY: [10, 10, 10, 10],
    });
    expect(sampleHeightAt(s, 0, -12)).toBeCloseTo(10, 1);
    expect(sampleHeightAt(s, 0, 12)).toBeCloseTo(10, 1);
  });
});

describe('carveRoadCorridor — run-off shoulder and berm', () => {
  it('keeps the run-off flat at bed level', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      shoulderWidth: 15,
      profileY: [10, 10, 10, 10],
    });
    // Bed edge at 10 m, run-off out to 25 m: all of it flat.
    expect(sampleHeightAt(s, 0, 12)).toBeCloseTo(10, 1);
    expect(sampleHeightAt(s, 0, 24)).toBeCloseTo(10, 1);
    // Past bed + run-off + falloff the natural relief is back.
    expect(sampleHeightAt(s, 0, 40)).toBeCloseTo(30, 0);
  });

  it('raises a berm at the outer edge of the run-off', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      shoulderWidth: 10,
      bermHeight: 2,
      bermWidth: 4,
      profileY: [10, 10, 10, 10],
    });
    expect(sampleHeightAt(s, 0, 18)).toBeCloseTo(10, 1); // run-off, flat
    expect(sampleHeightAt(s, 0, 24)).toBeCloseTo(12, 0); // berm crest
  });

  it('cuts a ditch for a negative berm', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: STRAIGHT,
      width: 20,
      falloff: 6,
      window: 40,
      shoulderWidth: 10,
      bermHeight: -3,
      bermWidth: 4,
      profileY: [10, 10, 10, 10],
    });
    expect(sampleHeightAt(s, 0, 24)).toBeCloseTo(7, 0);
  });
});

describe('carveRoadCorridor — closed loops', () => {
  /** Square circuit, last node duplicating the first. */
  const LOOP = [-100, -100, 100, -100, 100, 100, -100, 100, -100, -100];

  it('smooths the survey profile across the seam', () => {
    const open = hillySampler();
    const closed = hillySampler();
    const opts = { path: LOOP, width: 24, falloff: 8, window: 90 };
    carveRoadCorridor(open, { ...opts });
    carveRoadCorridor(closed, { ...opts, closed: true });
    // Sample either side of the start/finish node along the loop.
    const before = (s: HeightSampler) => sampleHeightAt(s, -100, -90);
    const after = (s: HeightSampler) => sampleHeightAt(s, -90, -100);
    const openStep = Math.abs(before(open) - after(open));
    const closedStep = Math.abs(before(closed) - after(closed));
    expect(closedStep).toBeLessThanOrEqual(openStep + 1e-6);
  });

  it('propagates the grade clamp through the seam', () => {
    const arcs = pathArcs(LOOP);
    // A plateau that has to come back down to the seam station.
    const heights = [0, 40, 40, 40, 0];
    const open = limitProfileGrade(arcs, heights, 0.05);
    const closed = limitProfileGrade(arcs, heights, 0.05, true);
    expect(closed[0]).toBeCloseTo(closed[closed.length - 1]!, 6);
    const grade = (h: number[], i: number) =>
      Math.abs(h[i + 1]! - h[i]!) / (arcs[i + 1]! - arcs[i]!);
    for (let i = 0; i < heights.length - 1; i++) {
      expect(grade(closed, i)).toBeLessThanOrEqual(0.05 + 1e-9);
    }
    // Open: the seam station ends up with two different heights — the loop
    // does not join, which is the step you see at the start/finish line.
    expect(Math.abs(open[0]! - open[open.length - 1]!)).toBeGreaterThan(1);
  });

  it('wraps the smoothing window', () => {
    const arcs = pathArcs(LOOP);
    const heights = [10, 0, 0, 0, 10];
    const closed = smoothProfile(arcs, heights, 600, true);
    expect(closed[0]).toBeCloseTo(closed[closed.length - 1]!, 6);
    // The seam station is pulled down by neighbours on BOTH sides.
    expect(closed[0]!).toBeLessThan(smoothProfile(arcs, heights, 600)[0]!);
  });

  it('keeps designRoadProfile closed end to end', () => {
    const arcs = pathArcs(LOOP);
    const h = designRoadProfile(arcs, [4, 9, 2, 7, 4], 120, 0.1, 2, true);
    expect(h[0]).toBeCloseTo(h[h.length - 1]!, 6);
  });
});

describe('carveRoadCorridor — self-crossing passes', () => {
  /**
   * Long hairpin: outbound at z=-20, return at z=+20, joined at x=300.
   * Enough nodes to trip the corridor index (>= 24 segments).
   */
  function hairpin(): { path: number[]; profile: number[] } {
    const path: number[] = [];
    const profile: number[] = [];
    for (let x = -300; x <= 300; x += 40) {
      path.push(x, -20);
      profile.push(5); // outbound bed low
    }
    for (let x = 300; x >= -300; x -= 40) {
      path.push(x, 20);
      profile.push(25); // return bed high
    }
    return { path, profile };
  }

  it('nearest mode lets the closest arm win the ground between them', () => {
    const { path, profile } = hairpin();
    const s = flatSampler(0.3, 512, 800);
    carveRoadCorridor(s, {
      path,
      width: 16,
      falloff: 4,
      window: 60,
      profileY: profile,
    });
    expect(sampleHeightAt(s, 0, -20)).toBeCloseTo(5, 0);
    expect(sampleHeightAt(s, 0, 20)).toBeCloseTo(25, 0);
  });

  it('closest-elevation mode keeps each arm on its own bed', () => {
    const { path, profile } = hairpin();
    const s = flatSampler(0.3, 512, 800); // natural ground at 30 m
    carveRoadCorridor(s, {
      path,
      width: 16,
      falloff: 4,
      window: 60,
      profileY: profile,
      overlapMode: 'closest-elevation',
      passSeparation: 100,
    });
    // Both beds are still carved to their own authored height…
    expect(sampleHeightAt(s, 0, 20)).toBeCloseTo(25, 0);
    // …and the high arm (nearer the 30 m natural ground) wins the strip
    // between them instead of the low one dragging it down.
    expect(sampleHeightAt(s, 0, 0)).toBeGreaterThan(15);
  });
});

describe('carveRoadCorridor — owner journal', () => {
  const opts = {
    path: STRAIGHT,
    width: 20,
    falloff: 6,
    window: 40,
    owner: 'road:test',
  };

  it('re-carving a surveyed bed does not sink it further', () => {
    const s = hillySampler();
    carveRoadCorridor(s, opts);
    const first = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, opts);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(first, 6);
    carveRoadCorridor(s, opts);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(first, 6);
  });

  it('without an owner the same carve keeps eating into the bed', () => {
    const s = hillySampler();
    const anon = { ...opts, owner: undefined };
    carveRoadCorridor(s, anon);
    const first = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, anon);
    expect(sampleHeightAt(s, 0, 0)).toBeLessThan(first);
  });

  it('reverts to the untouched terrain', () => {
    const s = hillySampler();
    const before = Float32Array.from(s.data!);
    carveRoadCorridor(s, opts);
    expect(revertHeightBrush(s, 'road:test')).toBe(true);
    expect(Array.from(s.data!)).toEqual(Array.from(before));
    // Journal consumed: a second revert is a no-op.
    expect(revertHeightBrush(s, 'road:test')).toBe(false);
  });

  it('leaves another owner’s carve alone', () => {
    const s = flatSampler(0.3);
    carveRoadCorridor(s, {
      path: [-150, 60, 150, 60],
      width: 20,
      falloff: 4,
      window: 40,
      profileY: [10, 10],
      owner: 'road:other',
    });
    carveRoadCorridor(s, { ...opts, profileY: [5, 5, 5, 5] });
    revertHeightBrush(s, 'road:test');
    expect(sampleHeightAt(s, 0, 60)).toBeCloseTo(10, 1);
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(30, 0);
  });
});
