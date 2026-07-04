import { describe, expect, it } from 'bun:test';
import { makeRiverGeometry } from '../../../src/plugins/water/river-geometry';

describe('makeRiverGeometry', () => {
  it('produces 3 vertices per path node (left bank, axis, right bank)', () => {
    const path = [0, 0, 10, 0, 10, 10]; // 3 nodes
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    expect(pos.count).toBe(3 * 3); // 3 nodes × 3 verts (left, axis, right)
  });

  it('aWaterT is 1 at banks and 0 at the axis (so depth/alpha interpolate)', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const t = geo.getAttribute('aWaterT');
    // Node 0: left bank t=1, axis t=0, right bank t=1.
    expect(t.array[0]).toBeCloseTo(1, 5);
    expect(t.array[1]).toBeCloseTo(0, 5);
    expect(t.array[2]).toBeCloseTo(1, 5);
  });

  it('UV.v spans 0..1 across the channel (left=0, axis=0.5, right=1)', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const uv = geo.getAttribute('uv');
    // Node 0: left v=0, axis v=0.5, right v=1.
    expect(uv.array[1]).toBeCloseTo(0, 5);
    expect(uv.array[3]).toBeCloseTo(0.5, 5);
    expect(uv.array[5]).toBeCloseTo(1, 5);
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

  it('winds triangles so the computed face normal points +Y (visible from above)', () => {
    // The water material uses default front-side rendering; a ribbon whose
    // normals point -Y would be backface-culled and invisible from above.
    // Verify via the geometry's computed normals (the source of truth).
    const geo = makeRiverGeometry([0, 0, 10, 0], 4);
    const n = geo.getAttribute('normal');
    const idx = geo.index!;
    // Average the y-component of the first triangle's vertex normals.
    const a = idx.array[0]!;
    const b = idx.array[1]!;
    const c = idx.array[2]!;
    const ny =
      (n.array[a * 3 + 1]! + n.array[b * 3 + 1]! + n.array[c * 3 + 1]!) / 3;
    expect(ny).toBeGreaterThan(0);
  });

  it('oversizes the ribbon slightly past width/2 (margin for alpha fade)', () => {
    const path = [0, 0, 10, 0];
    const geo = makeRiverGeometry(path, 4);
    const pos = geo.getAttribute('position');
    // Left bank of node 0 is vertex 0: position.z = -|offset|, |z| ≥ halfWidth (2).
    expect(Math.abs(pos.array[2])).toBeGreaterThanOrEqual(2);
  });

  it('throws on a path with fewer than 2 points', () => {
    expect(() => makeRiverGeometry([0, 0], 4)).toThrow(/at least 2 points/i);
  });
});
