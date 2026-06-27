import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  Terrain,
  getTerrainContext,
  sampleTerrainHeight,
} from 'vibegame';
import type { TerrainEntityData } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

function flatSampler(data: Float32Array | null = null): HeightSampler {
  return { width: 1, height: 1, data, worldSize: 256, maxHeight: 10 };
}

function makeTerrainField(
  overrides: Partial<TerrainEntityData> = {}
): TerrainEntityData {
  return {
    sampler: flatSampler(),
    chunks: new Set(),
    heightmapUrl: '/terrain/heightmap.png',
    initialized: false,
    collisionReady: false,
    worldOffset: { x: 0, y: 0, z: 0 },
    lastWireframe: 0,
    lastShowChunkBorders: 0,
    physicsBody: null,
    physicsCollider: null,
    chunkColliders: new Map(),
    ...overrides,
  };
}

describe('sampleTerrainHeight', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('terrain', Terrain);
  });

  it('returns 0 when no terrain field is registered', () => {
    expect(sampleTerrainHeight(state, 0, 0)).toBe(0);
  });

  it('returns 0 for a flat (undecoded) field', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(eid, makeTerrainField({ initialized: true }));
    expect(sampleTerrainHeight(state, 0, 0)).toBe(0);
  });

  it('returns the constant surface height for a uniform heightmap', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    Terrain.resolution[eid] = 2;
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        initialized: true,
        sampler: {
          width: 2,
          height: 2,
          data: new Float32Array([0.5, 0.5, 0.5, 0.5]),
          worldSize: 4,
          maxHeight: 10,
        },
      })
    );
    // Every footprint probe lands on the same uniform 0.5 amplitude → 5.0.
    expect(sampleTerrainHeight(state, 0, 0)).toBeCloseTo(5, 5);
  });

  it('reduces the cross footprint to its highest probe (max aggregation)', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    // resolution 0 → surfaceHeightAt falls back to the analytic bilinear height,
    // so the per-probe values are exactly computable.
    Terrain.resolution[eid] = 0;
    // X-ramp: left column amplitude 0, right column 1 (rows identical).
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        initialized: true,
        sampler: {
          width: 2,
          height: 2,
          data: new Float32Array([0, 1, 0, 1]),
          worldSize: 2,
          maxHeight: 10,
        },
      })
    );

    // Centre (0,0) → 5; +x(0.3) → 6.5; -x(0.3) → 3.5; ±z(0.3) → 5. Max = 6.5.
    expect(sampleTerrainHeight(state, 0, 0)).toBeCloseTo(6.5, 5);
  });

  it('returns only the centre probe when samples is 0', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    Terrain.resolution[eid] = 0;
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        initialized: true,
        sampler: {
          width: 2,
          height: 2,
          data: new Float32Array([0, 1, 0, 1]),
          worldSize: 2,
          maxHeight: 10,
        },
      })
    );

    // No cardinal offsets → just the centre probe (5), not the max (6.5).
    expect(sampleTerrainHeight(state, 0, 0, 0)).toBeCloseTo(5, 5);
  });
});
