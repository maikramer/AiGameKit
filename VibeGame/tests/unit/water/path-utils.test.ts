import { describe, expect, it } from 'bun:test';
import {
  distanceToPath,
  distanceToSegment,
  pathAabb,
  pathLength,
} from '../../../src/plugins/water/path-utils';

const SEG = [0, 0, 10, 0]; // one segment along +X from (0,0) to (10,0)
const ZIG = [0, 0, 10, 0, 10, 10]; // (0,0)→(10,0)→(10,10)

describe('pathAabb', () => {
  it('returns the bounding box of all points, expanded by pad', () => {
    expect(pathAabb(ZIG, 0)).toEqual({ minX: 0, minZ: 0, maxX: 10, maxZ: 10 });
    expect(pathAabb(ZIG, 2)).toEqual({
      minX: -2,
      minZ: -2,
      maxX: 12,
      maxZ: 12,
    });
  });
  it('handles a single segment', () => {
    expect(pathAabb(SEG, 1)).toEqual({ minX: -1, minZ: -1, maxX: 11, maxZ: 1 });
  });
});

describe('pathLength', () => {
  it('sums segment lengths', () => {
    expect(pathLength(SEG)).toBeCloseTo(10, 6);
    expect(pathLength(ZIG)).toBeCloseTo(20, 6);
  });
});

describe('distanceToSegment', () => {
  it('is 0 on the segment', () => {
    expect(distanceToSegment(5, 0, 0, 0, 10, 0)).toBeCloseTo(0, 6);
  });
  it('measures perpendicular distance to the infinite line when projecting inside', () => {
    expect(distanceToSegment(5, 3, 0, 0, 10, 0)).toBeCloseTo(3, 6);
  });
  it('falls back to endpoint distance past the ends', () => {
    expect(distanceToSegment(-4, 0, 0, 0, 10, 0)).toBeCloseTo(4, 6);
    expect(distanceToSegment(15, 0, 0, 0, 10, 0)).toBeCloseTo(5, 6);
  });
});

describe('distanceToPath', () => {
  it('returns the minimum distance to any segment', () => {
    // Point near the bend (10,0): closest to the first segment at distance 0.
    expect(distanceToPath(ZIG, 10, 0)).toBeCloseTo(0, 6);
    // Point at (5,5): 5 from each of the two segments.
    expect(distanceToPath(ZIG, 5, 5)).toBeCloseTo(5, 6);
    // Point at (13,5): closest to the vertical segment (10,0)-(10,10) → distance 3.
    expect(distanceToPath(ZIG, 13, 5)).toBeCloseTo(3, 6);
  });
  it('throws on a path with fewer than 2 points', () => {
    expect(() => distanceToPath([0, 0], 0, 0)).toThrow(/at least 2 points/i);
  });
});
