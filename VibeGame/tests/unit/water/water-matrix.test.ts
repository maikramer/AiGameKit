import { describe, expect, it } from 'bun:test';
import {
  distanceToPath,
  distanceToSegment,
  pathAabb,
  pathLength,
  resamplePath,
} from '../../../src/plugins/water/path-utils';

describe('water matrix: pathAabb pads', () => {
  const paths: Array<{ name: string; path: number[]; pad: number }> = [
    { name: 'unit square', path: [0, 0, 1, 0, 1, 1, 0, 1], pad: 0 },
    { name: 'diagonal', path: [0, 0, 10, 10], pad: 1 },
    { name: 'line x', path: [0, 0, 20, 0], pad: 2 },
  ];
  for (const p of paths) {
    it(`aabb ${p.name} pad=${p.pad}`, () => {
      const box = pathAabb(p.path, p.pad);
      expect(box.maxX - box.minX).toBeGreaterThan(0);
      expect(box.maxZ - box.minZ).toBeGreaterThanOrEqual(0);
    });
  }
});

describe('water matrix: pathLength', () => {
  it('zero-length degenerate segment', () => {
    expect(pathLength([0, 0, 0, 0])).toBe(0);
  });
  it('three-point path', () => {
    const len = pathLength([0, 0, 3, 0, 3, 4]);
    expect(len).toBeCloseTo(7, 5);
  });
  for (const segLen of [1, 5, 12.5, 100]) {
    it(`single segment length ${segLen}`, () => {
      expect(pathLength([0, 0, segLen, 0])).toBeCloseTo(segLen, 5);
    });
  }
});

describe('water matrix: resamplePath', () => {
  const base = [0, 0, 10, 0];
  for (const spacing of [0.5, 1, 2, 5]) {
    it(`spacing ${spacing} increases point count`, () => {
      const out = resamplePath(base, spacing);
      expect(out.length).toBeGreaterThanOrEqual(4);
      expect(out[0]).toBe(0);
      expect(out[1]).toBe(0);
    });
  }
  it('preserves end point', () => {
    const out = resamplePath(base, 1);
    expect(out[out.length - 2]).toBeCloseTo(10, 5);
    expect(out[out.length - 1]).toBeCloseTo(0, 5);
  });
});

describe('water matrix: distanceToSegment grid', () => {
  for (const px of [0, 5, 10]) {
    for (const pz of [0, 1, 3]) {
      it(`point (${px},${pz}) to segment along X`, () => {
        const d = distanceToSegment(px, pz, 0, 0, 10, 0);
        expect(d).toBeCloseTo(pz, 5);
      });
    }
  }
});

describe('water matrix: distanceToPath', () => {
  const path = [0, 0, 10, 0, 10, 10];
  const probes: Array<[number, number, number]> = [
    [0, 0, 0],
    [5, 0, 0],
    [5, 5, 5],
    [10, 5, 0],
    [12, 10, 2],
  ];
  for (const [x, z, maxD] of probes) {
    it(`distance at (${x},${z}) <= ${maxD}`, () => {
      expect(distanceToPath(path, x, z)).toBeLessThanOrEqual(maxD + 1e-6);
    });
  }
});

describe('water matrix: path errors', () => {
  it('pathAabb throws on short path', () => {
    expect(() => pathAabb([0, 0], 0)).toThrow(/at least 2 points/i);
  });
  it('resamplePath throws on short path', () => {
    expect(() => resamplePath([1, 2], 1)).toThrow(/at least 2 points/i);
  });
});

describe('water matrix: resamplePath L-shape', () => {
  it('corner point appears in output', () => {
    const zig = [0, 0, 5, 0, 5, 5];
    const out = resamplePath(zig, 1);
    let foundCorner = false;
    for (let i = 0; i + 1 < out.length; i += 2) {
      if (Math.abs(out[i]! - 5) < 1e-6 && Math.abs(out[i + 1]!) < 1e-6) {
        foundCorner = true;
      }
    }
    expect(foundCorner).toBe(true);
  });
});

describe('water matrix: monotonic resample length', () => {
  it('finer spacing yields more samples', () => {
    const path = [0, 0, 30, 0];
    const coarse = resamplePath(path, 5);
    const fine = resamplePath(path, 1);
    expect(fine.length).toBeGreaterThan(coarse.length);
  });
});
