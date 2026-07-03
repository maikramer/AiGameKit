import { describe, expect, it } from 'bun:test';
import { makeRiverGeometry } from '../../../src/plugins/water/river-geometry';

describe('makeRiverGeometry', () => {
  it('produces 2 vertices per path node (one ribbon row per side)', () => {
    const path = [0, 0, 10, 0, 10, 10]; // 3 nodes
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    expect(pos.count).toBe(3 * 2); // 3 nodes × 2 sides
  });

  it('aWaterT is 1 at both banks (|lateral| = halfWidth)', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const t = geo.getAttribute('aWaterT');
    // Node 0 has 2 verts, both at the bank → t = 1.
    expect(t.array[0]).toBeCloseTo(1, 5);
    expect(t.array[1]).toBeCloseTo(1, 5);
  });

  it('UV.v spans 0..1 across the channel width', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const uv = geo.getAttribute('uv');
    // First node: left v=0, right v=1 (or vice versa) — both edges present.
    expect(uv.array[1]).toBe(0);
    expect(uv.array[3]).toBe(1);
  });

  it('UV.u is 0 at the source and grows with accumulated length', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const geo = makeRiverGeometry(path, 4);
    const uv = geo.getAttribute('uv');
    // Source node u = 0.
    expect(uv.array[0]).toBeCloseTo(0, 5);
    // Last node u ≈ 20 (10 + 10).
    const lastU = uv.array[(uv.count - 1) * 2];
    expect(lastU).toBeCloseTo(20, 0);
  });

  it('has a non-empty index for the ribbon triangles', () => {
    const path = [0, 0, 10, 0, 10, 10];
    const geo = makeRiverGeometry(path, 4);
    expect(geo.index).not.toBeNull();
    expect(geo.index!.count).toBeGreaterThan(0);
  });

  it('oversizes the ribbon slightly past width/2 (margin for alpha fade)', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    // Left bank of node 0: position.x ~ 0, position.z = |offset| ≥ halfWidth (2).
    expect(Math.abs(pos.array[2])).toBeGreaterThanOrEqual(2);
  });

  it('throws on a path with fewer than 2 points', () => {
    expect(() => makeRiverGeometry([0, 0], 4)).toThrow(/at least 2 points/i);
  });
});
