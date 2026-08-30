import { describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  sampleMeshSurfaceHeight,
  sampleTerrainSurface,
  sampleTerrainSurfaceMatrix,
} from '../../../src/plugins/spawner/surface';
import { registerGroundBrush } from '../../../src/plugins/terrain/brush-registry';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import { Terrain } from '../../../src/plugins/terrain/components';

/**
 * Placement on a flatten-road talude must anchor to the analytic carved
 * heightfield (`worldY` = sampleHeightAt, `roadCarve` = true). Sampling the
 * mesh lattice there blends the cut with the uncut plateau — a quiet leaf
 * next to a density-boosted road leaf plants trees on the high lip while
 * the camera sees the green bank.
 *
 * Regression for simple-racer roadside oaks: `avoid-road` used to equal
 * bed+talude, so the forest sat on that lip and read as floating.
 */

const WORLD = 2000;
const MAX_H = 200;
const TRENCH = 80;
const PLATEAU = 100;
const MESH_RES = 64;

function buildState(): State {
  const state = new State();
  state.registerComponent('terrain', Terrain);
  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.resolution[terrain] = MESH_RES;
  Terrain.levels[terrain] = 4;

  const width = 257;
  const height = 257;
  const sampler: HeightSampler = {
    width,
    height,
    data: new Float32Array(width * height),
    worldSize: WORLD,
    maxHeight: MAX_H,
  };
  for (let z = 0; z < height; z++) {
    for (let x = 0; x < width; x++) {
      const lz = (z / (height - 1) - 0.5) * WORLD;
      const h = Math.abs(lz) < 16 ? TRENCH : PLATEAU;
      sampler.data![z * width + x] = h / MAX_H;
    }
  }

  getTerrainContext(state).set(terrain, {
    initialized: true,
    worldOffset: { x: 0, z: 0 },
    sampler,
    density: undefined,
  } as never);

  registerGroundBrush(state, {
    kind: 'road',
    minX: -50,
    maxX: 50,
    minZ: -20,
    maxZ: 20,
    path: [-40, 0, 40, 0],
    halfWidth: 4,
    carveHalfWidth: 16,
  });
  return state;
}

function samplerOf(state: State): HeightSampler {
  const ctx = getTerrainContext(state);
  for (const [, data] of ctx) return data.sampler;
  throw new Error('no sampler');
}

describe('sampleTerrainSurface road-carve anchor', () => {
  it('on the talude returns analytic height with roadCarve=true', () => {
    const state = buildState();
    const sampler = samplerOf(state);
    const wx = 10;
    const wz = 8;
    const analytic = sampleHeightAt(sampler, wx, wz);
    const lattice = sampleMeshSurfaceHeight(sampler, wx, wz, MESH_RES);
    expect(analytic).toBeCloseTo(TRENCH, 5);
    expect(lattice).toBeGreaterThan(analytic + 2);

    const s = sampleTerrainSurfaceMatrix(state, wx, wz, 0.75);
    expect(s).not.toBeNull();
    expect(s!.roadCarve).toBe(true);
    expect(s!.worldY).toBeCloseTo(analytic, 5);
    expect(s!.padPlane).toBeUndefined();

    const single = sampleTerrainSurface(state, wx, wz, 0.75);
    expect(single!.roadCarve).toBe(true);
    expect(single!.worldY).toBeCloseTo(analytic, 5);
  });

  it('outside the carve falls back to the mesh lattice (roadCarve unset)', () => {
    const state = buildState();
    const s = sampleTerrainSurfaceMatrix(state, 10, 40, 0.75);
    expect(s).not.toBeNull();
    expect(s!.roadCarve).toBeUndefined();
    expect(s!.worldY).toBeCloseTo(PLATEAU, 0);
  });
});
