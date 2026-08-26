import { describe, expect, it } from 'bun:test';
import {
  pointInRoadCarve,
  pointInRoadCorridor,
  type GroundBrush,
} from '../../../src/plugins/terrain/brush-registry';

/**
 * Road point queries go through a per-brush grid over the polyline. The grid is
 * an optimisation only: it must answer exactly what walking the whole path
 * would answer. Getting that wrong is invisible in a screenshot and shows up as
 * trees growing out of the asphalt.
 *
 * The scan it replaced cost 17 s of the RPG demo's 47 s spawn pass, blocking
 * the main thread long enough for in-flight GLBs to time out.
 */
function brute(path: number[], x: number, z: number): number {
  let best = Infinity;
  for (let i = 0; i + 3 < path.length; i += 2) {
    const ax = path[i]!;
    const az = path[i + 1]!;
    const dx = path[i + 2]! - ax;
    const dz = path[i + 3]! - az;
    const lenSq = dx * dx + dz * dz;
    let t = lenSq > 0 ? ((x - ax) * dx + (z - az) * dz) / lenSq : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    best = Math.min(best, Math.hypot(x - (ax + t * dx), z - (az + t * dz)));
  }
  return best;
}

/** Winding road, long enough that a linear scan would be the slow path. */
function makeRoad(halfWidth: number, carveHalfWidth?: number): GroundBrush {
  const path: number[] = [];
  for (let i = 0; i <= 400; i++) {
    const t = i * 2;
    path.push(t, Math.sin(i * 0.15) * 40);
  }
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < path.length; i += 2) {
    minX = Math.min(minX, path[i]!);
    maxX = Math.max(maxX, path[i]!);
    minZ = Math.min(minZ, path[i + 1]!);
    maxZ = Math.max(maxZ, path[i + 1]!);
  }
  const pad = Math.max(halfWidth, carveHalfWidth ?? 0);
  return {
    kind: 'road',
    minX: minX - pad,
    maxX: maxX + pad,
    minZ: minZ - pad,
    maxZ: maxZ + pad,
    path,
    halfWidth,
    carveHalfWidth,
  };
}

describe('road point queries via the path grid', () => {
  it('agrees with a full polyline walk across the map', () => {
    const half = 5;
    const brush = makeRoad(half);
    let checked = 0;
    let onRoad = 0;
    for (let x = -20; x <= 820; x += 7) {
      for (let z = -60; z <= 60; z += 3.5) {
        const expected = brute(brush.path!, x, z) <= half;
        expect(pointInRoadCorridor(brush, x, z)).toBe(expected);
        checked++;
        if (expected) onRoad++;
      }
    }
    // Guard the guard: a test where nothing is ever on the road proves nothing.
    expect(checked).toBeGreaterThan(2000);
    expect(onRoad).toBeGreaterThan(50);
  });

  it('uses the carve width for the carve query, not the corridor width', () => {
    const brush = makeRoad(4, 12);
    // Between the two widths: off the asphalt, still on the carved shelf.
    const path = brush.path!;
    const x = path[100]!;
    const z = path[101]! + 8;
    expect(pointInRoadCorridor(brush, x, z)).toBe(false);
    expect(pointInRoadCarve(brush, x, z)).toBe(true);
  });

  it('answers false far outside the path bounds', () => {
    const brush = makeRoad(5);
    expect(pointInRoadCorridor(brush, -5000, -5000)).toBe(false);
    expect(pointInRoadCarve(brush, 5000, 5000)).toBe(false);
  });

  it('never reports a flying span as paved ground', () => {
    const brush = makeRoad(5);
    brush.flying = true;
    const path = brush.path!;
    expect(pointInRoadCorridor(brush, path[0]!, path[1]!)).toBe(false);
    expect(pointInRoadCarve(brush, path[0]!, path[1]!)).toBe(false);
  });

  it('handles a degenerate two-point path', () => {
    const brush: GroundBrush = {
      kind: 'road',
      minX: -10,
      maxX: 20,
      minZ: -10,
      maxZ: 10,
      path: [0, 0, 10, 0],
      halfWidth: 2,
    };
    expect(pointInRoadCorridor(brush, 5, 1)).toBe(true);
    expect(pointInRoadCorridor(brush, 5, 3)).toBe(false);
  });
});
