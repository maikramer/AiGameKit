import { describe, expect, it } from 'bun:test';
import {
  createCorridorIndex,
  nearestCorridorPasses,
  nearestOnCorridor,
  nearestOnPolyline,
  pathArcs,
  resampleNodeValues,
} from '../../../src/plugins/terrain/corridor';

/** Zig-zag polyline with `n` nodes, `step` metres apart along +X. */
function zigZag(n: number, step = 5, amp = 3): number[] {
  const p: number[] = [];
  for (let i = 0; i < n; i++) {
    p.push(i * step, i % 2 === 0 ? -amp : amp);
  }
  return p;
}

describe('nearestOnPolyline signed side', () => {
  const path = [0, 0, 100, 0];

  it('is positive right of travel and negative left', () => {
    // Engine convention (TrackSpline): right = (tangentZ, -tangentX), so
    // travelling +X the driver's right is -Z.
    expect(nearestOnPolyline(path, 50, -4)!.signed).toBeCloseTo(4, 6);
    expect(nearestOnPolyline(path, 50, 4)!.signed).toBeCloseTo(-4, 6);
  });

  it('keeps |signed| equal to dist', () => {
    const n = nearestOnPolyline(path, 20, -7)!;
    expect(Math.abs(n.signed)).toBeCloseTo(n.dist, 6);
  });

  it('reports arc position along the whole polyline', () => {
    const bent = [0, 0, 10, 0, 10, 10];
    expect(nearestOnPolyline(bent, 5, 0)!.arc).toBeCloseTo(5, 6);
    expect(nearestOnPolyline(bent, 10, 6)!.arc).toBeCloseTo(16, 6);
  });
});

describe('pathArcs', () => {
  it('accumulates segment lengths', () => {
    expect(pathArcs([0, 0, 3, 4, 3, 10])).toEqual([0, 5, 11]);
  });

  it('returns empty for an empty path', () => {
    expect(pathArcs([])).toEqual([]);
  });
});

describe('createCorridorIndex', () => {
  it('returns null for a degenerate path', () => {
    expect(createCorridorIndex([1, 2], 10)).toBeNull();
  });

  it('matches the brute-force nearest inside reach', () => {
    const path = zigZag(60);
    const index = createCorridorIndex(path, 12)!;
    for (let i = 0; i < 200; i++) {
      const x = -20 + (i * 320) / 200;
      const z = -14 + ((i * 7) % 29);
      const brute = nearestOnPolyline(path, x, z)!;
      const fast = nearestOnCorridor(index, x, z);
      if (brute.dist <= 12) {
        expect(fast).not.toBeNull();
        expect(fast!.dist).toBeCloseTo(brute.dist, 6);
        expect(fast!.arc).toBeCloseTo(brute.arc, 6);
      }
    }
  });

  it('returns null beyond the reach it was built for', () => {
    const index = createCorridorIndex([0, 0, 100, 0], 5)!;
    expect(nearestOnCorridor(index, 50, 40)).toBeNull();
    expect(nearestOnCorridor(index, 50, 2)).not.toBeNull();
  });

  it('keeps working when a huge span forces the cell to grow', () => {
    const index = createCorridorIndex([-20000, -20000, 20000, 20000], 0.5)!;
    expect(index.cell).toBeGreaterThan(0.5);
    expect(nearestOnCorridor(index, 0, 0)!.dist).toBeCloseTo(0, 3);
  });
});

describe('nearestCorridorPasses', () => {
  // Two parallel arms 6 m apart joined at the far end — a hairpin.
  const hairpin = [0, 0, 100, 0, 100, 6, 0, 6];

  it('reports both arms as distinct passes', () => {
    const index = createCorridorIndex(hairpin, 8)!;
    const passes = nearestCorridorPasses(index, 50, 3, 20);
    expect(passes.length).toBe(2);
    expect(passes[0]!.dist).toBeCloseTo(3, 6);
    expect(passes[1]!.dist).toBeCloseTo(3, 6);
    // One from the outbound arm, one from the return arm.
    expect(Math.abs(passes[0]!.arc - passes[1]!.arc)).toBeGreaterThan(20);
  });

  it('collapses neighbouring segments of one pass into a single candidate', () => {
    const index = createCorridorIndex(zigZag(40), 10)!;
    const passes = nearestCorridorPasses(index, 60, 0, 60);
    expect(passes.length).toBe(1);
  });

  it('is empty outside the corridor', () => {
    const index = createCorridorIndex(hairpin, 8)!;
    expect(nearestCorridorPasses(index, 50, 60, 20)).toEqual([]);
  });
});

describe('resampleNodeValues', () => {
  it('maps by arc fraction, pinning both ends', () => {
    const src = [0, 0, 10, 0];
    const dst = [0, 0, 5, 0, 10, 0];
    expect(resampleNodeValues(src, [2, 6], dst)).toEqual([2, 4, 6]);
  });

  it('survives a shorter (smoothed) destination path', () => {
    const src = [0, 0, 10, 0, 20, 0];
    // Same shape, 20% shorter: fractions still line up.
    const dst = [0, 0, 8, 0, 16, 0];
    expect(resampleNodeValues(src, [0, 10, 20], dst)).toEqual([0, 10, 20]);
  });

  it('fills a constant when only one source value exists', () => {
    expect(resampleNodeValues([0, 0], [7], [0, 0, 1, 0])).toEqual([7, 7]);
  });

  it('returns empty for an empty destination', () => {
    expect(resampleNodeValues([0, 0, 1, 0], [1, 2], [])).toEqual([]);
  });
});
