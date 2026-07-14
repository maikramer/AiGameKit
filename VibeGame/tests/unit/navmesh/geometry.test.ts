import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  buildAdaptiveTerrainGeometry,
  collectNavmeshGeometry,
  collectWaterObstacles,
} from '../../../src/plugins/navmesh/geometry';
import {
  registerGroundBrush,
  clearGroundBrushes,
} from '../../../src/plugins/terrain/brush-registry';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import type { TerrainEntityData } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
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

function makeField(
  overrides: Partial<TerrainEntityData> = {}
): TerrainEntityData {
  const sampler: HeightSampler = {
    width: 4,
    height: 4,
    // Non-zero amplitudes so pad override is distinguishable from heightmap.
    data: new Float32Array(16).fill(0.2),
    worldSize: 256,
    maxHeight: 50,
  };
  return {
    sampler,
    chunks: new Set(),
    heightmapUrl: undefined,
    initialized: true,
    collisionReady: true,
    worldOffset: { x: 0, y: 0, z: 0 },
    lastWireframe: 0,
    lastShowChunkBorders: 0,
    physicsBody: null,
    physicsCollider: null,
    chunkColliders: new Map(),
    ...overrides,
  };
}

describe('collectWaterObstacles', () => {
  it('returns null when no water bodies are registered', () => {
    const state = new State();
    expect(collectWaterObstacles(state, 120)).toBeNull();
  });

  it('emits a vertical cylinder per registered lake', () => {
    const state = new State();
    const body: WaterBody = {
      kind: 'lake',
      x: 5,
      z: -3,
      radius: 6,
      shoreRadius: 4.2,
      waterY: 10,
    };
    registerWaterBody(state, body);

    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    expect(geom!.positions).toBeInstanceOf(Float32Array);
    expect(geom!.indices).toBeInstanceOf(Uint32Array);

    expect(geom!.positions.length).toBe(EXPECT_VERTS * 3);
    expect(geom!.indices.length).toBe(EXPECT_TRIS * 3);

    const p = geom!.positions;
    for (let i = 0; i < p.length; i += 3) {
      const dx = p[i]! - body.x;
      const dz = p[i + 2]! - body.z;
      expect(Math.hypot(dx, dz)).toBeCloseTo(body.radius, 3);
      expect(p[i + 1]!).toBeGreaterThanOrEqual(body.waterY - 1.0 - 1e-6);
      expect(p[i + 1]!).toBeLessThanOrEqual(body.waterY + 1.5 + 1e-6);
    }

    unregisterWaterBody(state, body);
  });

  it('emits one cylinder per body when several lakes overlap the bake area', () => {
    const state = new State();
    const a: WaterBody = {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 4,
      shoreRadius: 2.8,
      waterY: 0,
    };
    const b: WaterBody = {
      kind: 'lake',
      x: 20,
      z: -10,
      radius: 5,
      shoreRadius: 3.5,
      waterY: 3,
    };
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
    const far: WaterBody = {
      kind: 'lake',
      x: 500,
      z: 500,
      radius: 6,
      shoreRadius: 4.2,
      waterY: 0,
    };
    registerWaterBody(state, far);
    expect(collectWaterObstacles(state, 120)).toBeNull();
    unregisterWaterBody(state, far);
  });

  it('keeps a lake that merely clips the edge of the bake area', () => {
    const state = new State();
    const edge: WaterBody = {
      kind: 'lake',
      x: 122,
      z: 0,
      radius: 6,
      shoreRadius: 4.2,
      waterY: 0,
    };
    registerWaterBody(state, edge);
    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    unregisterWaterBody(state, edge);
  });

  it('emits ribbon walls for a registered river', () => {
    const state = new State();
    const river: WaterBody = {
      kind: 'river',
      path: [
        [0, -20],
        [0, 0],
        [0, 20],
      ],
      width: 8,
      waterY: 5,
    };
    registerWaterBody(state, river);
    const geom = collectWaterObstacles(state, 120);
    expect(geom).not.toBeNull();
    // 2 segments × 2 banks × 2 tris × 3 indices
    expect(geom!.indices.length).toBe(2 * 2 * 2 * 3);
    expect(geom!.positions.length).toBe(2 * 2 * 4 * 3);
    unregisterWaterBody(state, river);
  });

  it('is merged into collectNavmeshGeometry even without terrain', () => {
    const state = new State();
    registerWaterBody(state, {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 6,
      shoreRadius: 4.2,
      waterY: 0,
    });

    const geom = collectNavmeshGeometry(state, 64, 60);
    expect(geom.indices.length).toBeGreaterThan(0);
  });
});

describe('buildAdaptiveTerrainGeometry', () => {
  it('uses pad targetY inside the flat core', () => {
    const state = new State();
    const eid = state.createEntity();
    getTerrainContext(state).set(eid, makeField());
    registerGroundBrush(state, {
      kind: 'pad',
      minX: -20,
      maxX: 20,
      minZ: -20,
      maxZ: 20,
      halfX: 12,
      halfZ: 12,
      cornerRadius: 2,
      targetY: 7.5,
    });

    const geom = buildAdaptiveTerrainGeometry(state, 16, 30);
    expect(geom).not.toBeNull();
    // Centre vertex of the coarse grid should sit on the pad plane.
    let found = false;
    const p = geom!.positions;
    for (let i = 0; i < p.length; i += 3) {
      if (Math.abs(p[i]!) < 0.01 && Math.abs(p[i + 2]!) < 0.01) {
        expect(p[i + 1]!).toBeCloseTo(7.5, 5);
        found = true;
        break;
      }
    }
    expect(found).toBe(true);
    clearGroundBrushes(state);
  });

  it('emits fewer base tris than a uniform 180² grid for the same bounds', () => {
    const state = new State();
    const eid = state.createEntity();
    getTerrainContext(state).set(eid, makeField());
    // divisions=96 → 96² * 2 tris; uniform 180 → 180² * 2
    const adaptive = buildAdaptiveTerrainGeometry(state, 96, 120);
    expect(adaptive).not.toBeNull();
    const adaptiveTris = adaptive!.indices.length / 3;
    const uniform180Tris = 180 * 180 * 2;
    expect(adaptiveTris).toBeLessThan(uniform180Tris);
    // Pure base grid (no dense patches): exactly 96² * 2
    expect(adaptiveTris).toBe(96 * 96 * 2);
  });
});
