import { beforeEach, describe, expect, it } from 'bun:test';
import { State, Terrain, getTerrainContext, terrainReady } from 'vibegame';
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

describe('terrainReady', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('terrain', Terrain);
  });

  it('is ready when the scene declares no terrain', () => {
    expect(terrainReady(state)).toBe(true);
  });

  it('is not ready while a terrain field is uninitialized', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(eid, makeTerrainField());
    expect(terrainReady(state)).toBe(false);
  });

  it('is not ready when the heightmap is initialized but not yet decoded', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(eid, makeTerrainField({ initialized: true }));
    expect(terrainReady(state)).toBe(false);
  });

  it('is not ready when the heightmap is decoded but the heightfield is still building', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        initialized: true,
        sampler: flatSampler(new Float32Array([0])),
        collisionReady: false,
      })
    );
    expect(terrainReady(state)).toBe(false);
  });

  it('is ready when every field is initialized, decoded, and collision-ready', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        initialized: true,
        sampler: flatSampler(new Float32Array([0])),
        collisionReady: true,
      })
    );
    expect(terrainReady(state)).toBe(true);
  });

  it('is ready for a flat (no heightmap URL) field once initialized and collision-ready', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Terrain);
    getTerrainContext(state).set(
      eid,
      makeTerrainField({
        heightmapUrl: undefined,
        initialized: true,
        collisionReady: true,
      })
    );
    expect(terrainReady(state)).toBe(true);
  });
});
