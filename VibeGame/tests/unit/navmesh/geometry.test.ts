import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  collectNavmeshGeometry,
  collectWaterObstacles,
} from '../../../src/plugins/navmesh/geometry';
import {
  registerWaterBody,
  unregisterWaterBody,
} from '../../../src/plugins/water/registry';
import type { WaterBody } from '../../../src/plugins/water/registry';

// 16-segment open cylinder tube: 32 vertices (two rings), 16 side quads -> 32
// triangles.
const SEG = 16;
const EXPECT_VERTS = SEG * 2;
const EXPECT_TRIS = SEG * 2;

describe('collectWaterObstacles', () => {
  it('returns null when no water bodies are registered', () => {
    const state = new State();
    expect(collectWaterObstacles(state, 120)).toBeNull();
  });

  it('emits a vertical cylinder per registered lake', () => {
    const state = new State();
    const body: WaterBody = { x: 5, z: -3, radius: 6, waterY: 10 };
    registerWaterBody(state, body);

    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    expect(geom!.positions).toBeInstanceOf(Float32Array);
    expect(geom!.indices).toBeInstanceOf(Uint32Array);

    expect(geom!.positions.length).toBe(EXPECT_VERTS * 3);
    expect(geom!.indices.length).toBe(EXPECT_TRIS * 3);

    const p = geom!.positions;
    for (let i = 0; i < p.length; i += 3) {
      const dx = p[i] - body.x;
      const dz = p[i + 2] - body.z;
      expect(Math.hypot(dx, dz)).toBeCloseTo(body.radius, 3);
      expect(p[i + 1]).toBeGreaterThanOrEqual(body.waterY - 1.0 - 1e-6);
      expect(p[i + 1]).toBeLessThanOrEqual(body.waterY + 1.5 + 1e-6);
    }

    unregisterWaterBody(state, body);
  });

  it('emits one cylinder per body when several lakes overlap the bake area', () => {
    const state = new State();
    const a: WaterBody = { x: 0, z: 0, radius: 4, waterY: 0 };
    const b: WaterBody = { x: 20, z: -10, radius: 5, waterY: 3 };
    registerWaterBody(state, a);
    registerWaterBody(state, b);

    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    expect(geom!.positions.length).toBe(EXPECT_VERTS * 3 * 2);

    unregisterWaterBody(state, a);
    unregisterWaterBody(state, b);
  });

  it('skips lakes whose disc lies entirely outside the bake bounds', () => {
    const state = new State();
    const far: WaterBody = { x: 500, z: 500, radius: 6, waterY: 0 };
    registerWaterBody(state, far);
    expect(collectWaterObstacles(state, 120)).toBeNull();
    unregisterWaterBody(state, far);
  });

  it('keeps a lake that merely clips the edge of the bake area', () => {
    const state = new State();
    const edge: WaterBody = { x: 122, z: 0, radius: 6, waterY: 0 };
    registerWaterBody(state, edge);
    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    unregisterWaterBody(state, edge);
  });

  it('is merged into collectNavmeshGeometry even without terrain', () => {
    const state = new State();
    registerWaterBody(state, { x: 0, z: 0, radius: 6, waterY: 0 });

    const geom = collectNavmeshGeometry(state, 64, 60);
    expect(geom.indices.length).toBeGreaterThan(0);
  });
});
