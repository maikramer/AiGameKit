import { afterEach, describe, expect, it } from 'bun:test';
import {
  addTrackRamp,
  clearTrackData,
  clearTrackObstacles,
  clearTrackSpaceObstacles,
} from '../../../src/plugins/racing/data';
import {
  TrackSpline,
  type TrackNode,
} from '../../../src/plugins/racing/spline';
import {
  generateItemBoxRows,
  generateObstacles,
  mulberry32,
} from '../../../src/plugins/racing/layouts';
import {
  ObstacleKind,
  ObstacleMoveMode,
} from '../../../src/plugins/racing/components';

function ovalNodes(width = 16): TrackNode[] {
  return [
    { x: 0, y: 0, z: -600, width },
    { x: 600, y: 0, z: -600, width },
    { x: 1200, y: 0, z: -600, width },
    { x: 1500, y: 0, z: 0, width },
    { x: 1200, y: 0, z: 600, width },
    { x: 0, y: 0, z: 600, width },
    { x: -1200, y: 0, z: 600, width },
    { x: -1500, y: 0, z: 0, width },
    { x: -1200, y: 0, z: -600, width },
    { x: -600, y: 0, z: -600, width },
  ];
}

afterEach(() => {
  clearTrackData();
});

describe('hazard layouts', () => {
  it('the same seed reproduces the same layout exactly', () => {
    const spline = new TrackSpline(ovalNodes(), { step: 2 });
    const a = generateItemBoxRows(spline, mulberry32(42), 6, 3);
    const b = generateItemBoxRows(spline, mulberry32(42), 6, 3);
    expect(a.length).toBe(b.length);
    expect(a.length).toBe(18);
    for (let i = 0; i < a.length; i++) {
      expect(a[i]!.s).toBeCloseTo(b[i]!.s);
      expect(a[i]!.lateral).toBeCloseTo(b[i]!.lateral);
    }
    // Different seed, different layout.
    const c = generateItemBoxRows(spline, mulberry32(43), 6, 3);
    expect(c.some((p, i) => Math.abs(p.s - a[i]!.s) > 1)).toBe(true);
  });

  it('spreads box rows apart and spans them across the road', () => {
    const spline = new TrackSpline(ovalNodes(), { step: 2 });
    const rows = generateItemBoxRows(spline, mulberry32(7), 5, 3);
    const uniqueS = new Set(rows.map((r) => Math.round(r.s)));
    expect(uniqueS.size).toBe(5);
    for (const s of uniqueS) {
      const laterals = rows
        .filter((r) => Math.round(r.s) === s)
        .map((r) => r.lateral);
      expect(laterals.length).toBe(3);
      // Rows span the road: outermost boxes sit either side of the centreline.
      expect(Math.min(...laterals)).toBeLessThan(-2);
      expect(Math.max(...laterals)).toBeGreaterThan(2);
    }
  });

  it('keeps obstacles away from ramps and box rows', () => {
    const spline = new TrackSpline(ovalNodes(), { step: 2 });
    addTrackRamp(200, 12, 8, 2.5);
    const rows = generateItemBoxRows(spline, mulberry32(11), 5, 3);
    const rowS = [...new Set(rows.map((r) => r.s))];
    const specs = generateObstacles(
      spline,
      mulberry32(11),
      { obstacles: 4, moving: 2, crates: 2 },
      rowS
    );
    expect(specs.length).toBe(8);
    for (const spec of specs) {
      // Clear of the ramp corridor (25 m before, 45 m after the span).
      expect(spec.s > 200 - 25 && spec.s < 200 + 12 + 45).toBe(false);
      // Clear of every box row by the cross-gap.
      for (const s of rowS) {
        expect(Math.abs(spline.deltaS(spec.s, s))).toBeGreaterThanOrEqual(40);
      }
    }
    // The mix contains every family: parked, moving and breakable.
    expect(
      specs.filter((s) => s.moveMode === ObstacleMoveMode.Static).length
    ).toBe(4 + 2); // parked hazards + crates
    expect(
      specs.filter((s) => s.moveMode !== ObstacleMoveMode.Static).length
    ).toBe(2);
    expect(specs.filter((s) => s.breakable === 1).length).toBe(2);
    expect(specs.some((s) => s.kind === ObstacleKind.Crate)).toBe(true);
    clearTrackObstacles();
    clearTrackSpaceObstacles();
  });
});
