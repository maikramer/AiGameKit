import { describe, expect, it } from 'bun:test';
import {
  TrackSpline,
  createFrame,
  nodesFromFlatList,
} from '../../../src/plugins/racing/spline';
import type { TrackNode } from '../../../src/plugins/racing/spline';

/** A 200 m × 200 m flat square loop with rounded corners (Catmull-Rom does the rest). */
function squareLoop(width = 12): TrackNode[] {
  return [
    { x: -100, y: 0, z: -100, width },
    { x: 100, y: 0, z: -100, width },
    { x: 100, y: 0, z: 100, width },
    { x: -100, y: 0, z: 100, width },
  ];
}

/** A figure-of-eight in plan view where the crossing point is at two heights. */
function flyoverLoop(): TrackNode[] {
  return [
    { x: 0, y: 0, z: 0 },
    { x: 120, y: 0, z: 40 },
    { x: 160, y: 4, z: 140 },
    { x: 60, y: 10, z: 200 },
    // Comes back across the start of the lap, 10 m higher.
    { x: -40, y: 10, z: 120 },
    { x: 20, y: 6, z: 20 },
    { x: 120, y: 3, z: -60 },
    { x: 20, y: 0, z: -100 },
    { x: -80, y: 0, z: -40 },
  ];
}

describe('TrackSpline geometry', () => {
  it('resamples at a uniform arc length and closes without a seam', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2 });
    expect(spline.count).toBeGreaterThan(100);
    // step is snapped so count * step lands exactly on the total length.
    expect(spline.count * spline.step).toBeCloseTo(spline.length, 3);

    const a = spline.sampleAt(0);
    const b = spline.sampleAt(spline.length);
    expect(b.x).toBeCloseTo(a.x, 3);
    expect(b.z).toBeCloseTo(a.z, 3);
  });

  it('spaces consecutive samples evenly', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2 });
    const f = createFrame();
    const g = createFrame();
    for (let s = 0; s < spline.length - 10; s += 37) {
      spline.sampleAt(s, f);
      spline.sampleAt(s + spline.step, g);
      const d = Math.hypot(g.x - f.x, g.y - f.y, g.z - f.z);
      expect(d).toBeGreaterThan(spline.step * 0.8);
      expect(d).toBeLessThan(spline.step * 1.2);
    }
  });

  it('builds a right-handed frame whose up vector points at the sky', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2, maxAutoBank: 0 });
    const f = createFrame();
    for (let s = 0; s < spline.length; s += 25) {
      spline.sampleAt(s, f);
      expect(f.uy).toBeGreaterThan(0.9);
      // tangent ⟂ right, tangent ⟂ up
      expect(Math.abs(f.tx * f.rx + f.ty * f.ry + f.tz * f.rz)).toBeLessThan(
        1e-3
      );
      expect(Math.abs(f.tx * f.ux + f.ty * f.uy + f.tz * f.uz)).toBeLessThan(
        1e-3
      );
    }
  });

  it("points `right` to the driver's right (+X when heading +Z)", () => {
    const spline = new TrackSpline(
      [
        { x: 0, y: 0, z: -100 },
        { x: 0, y: 0, z: 0 },
        { x: 0, y: 0, z: 100 },
        { x: 40, y: 0, z: 160 },
        { x: 40, y: 0, z: -160 },
      ],
      { step: 2, closed: true, maxAutoBank: 0 }
    );
    const f = createFrame();
    // Find a sample heading roughly +Z and check its right vector.
    for (let s = 0; s < spline.length; s += spline.step) {
      spline.sampleAt(s, f);
      if (f.tz > 0.97) {
        expect(f.rx).toBeGreaterThan(0.9);
        return;
      }
    }
    throw new Error('no +Z heading sample found');
  });

  it('banks into the corner: a left-hander raises the right-hand edge', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2, maxAutoBank: 12 });
    const f = createFrame();
    let checked = 0;
    for (let s = 0; s < spline.length; s += spline.step) {
      spline.sampleAt(s, f);
      if (f.curvature > 0.01) {
        // Turning left → positive bank → the right edge sits higher.
        expect(f.bank).toBeGreaterThan(0);
        expect(f.ry).toBeGreaterThan(0);
        checked++;
      } else if (f.curvature < -0.01) {
        expect(f.bank).toBeLessThan(0);
        checked++;
      }
    }
    expect(checked).toBeGreaterThan(10);
  });

  it('wraps arc positions and measures the shortest signed delta', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2 });
    const L = spline.length;
    expect(spline.wrapS(-1)).toBeCloseTo(L - 1, 4);
    expect(spline.wrapS(L + 5)).toBeCloseTo(5, 4);
    expect(spline.deltaS(2, L - 2)).toBeCloseTo(4, 4);
    expect(spline.deltaS(L - 2, 2)).toBeCloseTo(-4, 4);
  });
});

describe('TrackSpline projection', () => {
  it('recovers the arc position and lateral offset of a known point', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2, maxAutoBank: 0 });
    const s = 137;
    const p = spline.positionAt(s, 3.5);
    const proj = spline.project(p.x, p.y, p.z, s);
    expect(proj.s).toBeCloseTo(s, 1);
    expect(proj.lateral).toBeCloseTo(3.5, 2);
    expect(proj.height).toBeCloseTo(0, 3);
  });

  it('finds the track from a cold start with no hint', () => {
    const spline = new TrackSpline(squareLoop(), { step: 2, maxAutoBank: 0 });
    const p = spline.positionAt(400, -2);
    const proj = spline.project(p.x, p.y, p.z, null);
    expect(proj.s).toBeCloseTo(400, 0);
    expect(proj.lateral).toBeCloseTo(-2, 1);
  });

  it('stays on its own branch where the circuit passes over itself', () => {
    // This is the bug the old XZ-only projection could not express: two parts of
    // the circuit share a plan-view location at different heights, and the
    // lower branch used to capture cars driving the upper one.
    const spline = new TrackSpline(flyoverLoop(), { step: 2 });

    // Find two arc positions that are far apart along the track but close in XZ.
    let lowS = -1;
    let highS = -1;
    const a = createFrame();
    const b = createFrame();
    outer: for (let s1 = 0; s1 < spline.length; s1 += 4) {
      spline.sampleAt(s1, a);
      for (let s2 = s1 + 150; s2 < spline.length; s2 += 4) {
        spline.sampleAt(s2, b);
        if (Math.hypot(a.x - b.x, a.z - b.z) < 6 && Math.abs(a.y - b.y) > 3) {
          lowS = a.y < b.y ? s1 : s2;
          highS = a.y < b.y ? s2 : s1;
          break outer;
        }
      }
    }
    expect(lowS).toBeGreaterThanOrEqual(0);

    const high = spline.sampleAt(highS, a);
    const hinted = spline.project(high.x, high.y, high.z, highS);
    expect(Math.abs(spline.deltaS(hinted.s, highS))).toBeLessThan(4);
    expect(Math.abs(hinted.height)).toBeLessThan(1);
  });

  it('reports curvature ahead so a driver can brake for the corner it sees', () => {
    // Mid-side nodes give each side a genuine straight to measure from.
    const spline = new TrackSpline(
      [
        { x: -100, y: 0, z: -100 },
        { x: 0, y: 0, z: -100 },
        { x: 100, y: 0, z: -100 },
        { x: 100, y: 0, z: 0 },
        { x: 100, y: 0, z: 100 },
        { x: 0, y: 0, z: 100 },
        { x: -100, y: 0, z: 100 },
        { x: -100, y: 0, z: 0 },
      ],
      { step: 2 }
    );
    let straightS = -1;
    const f = createFrame();
    for (let s = 0; s < spline.length; s += spline.step) {
      spline.sampleAt(s, f);
      if (Math.abs(f.curvature) < 0.001) {
        straightS = s;
        break;
      }
    }
    expect(straightS).toBeGreaterThanOrEqual(0);
    const ahead = spline.maxCurvatureAhead(straightS, spline.length / 3);
    expect(Math.abs(ahead)).toBeGreaterThan(0.005);
  });
});

describe('nodesFromFlatList', () => {
  it('reads xyz triples', () => {
    const nodes = nodesFromFlatList([0, 1, 2, 3, 4, 5], 3);
    expect(nodes).toHaveLength(2);
    expect(nodes[1]).toEqual({ x: 3, y: 4, z: 5 });
  });

  it('reads xyzw quads and per-node sections', () => {
    const nodes = nodesFromFlatList([0, 0, 0, 14, 10, 0, 0, 9], 4, ['a', 'b']);
    expect(nodes[0]!.width).toBe(14);
    expect(nodes[1]!.width).toBe(9);
    expect(nodes[0]!.section).toBe('a');
    expect(nodes[1]!.section).toBe('b');
  });

  it('rejects a spline with fewer than two nodes', () => {
    expect(() => new TrackSpline([{ x: 0, y: 0, z: 0 }])).toThrow();
  });
});
