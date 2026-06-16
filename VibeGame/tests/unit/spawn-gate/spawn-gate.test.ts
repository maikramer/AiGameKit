import { describe, expect, it, beforeEach } from 'bun:test';
import {
  State,
  Transform,
  Rigidbody,
  getTerrainContext,
  getBvhSurfaceHeight,
  getTerrainHeightAt,
} from 'vibegame';
import type { TerrainEntityData } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  SpawnGateComponent,
  SpawnGateSystem,
  gateEntity,
} from '../../../src/plugins/spawn-gate';

/** Flat heightmap sampler: every query returns 0 (sea level). */
function flatSampler(data: Float32Array | null = null): HeightSampler {
  return { width: 1, height: 1, data, worldSize: 256, maxHeight: 10 };
}

/** Build a TerrainEntityData with sensible defaults + overrides. */
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

// No Collider on the hero → snap target is ground + skin, keeping assertions deterministic.
function makeGatedHero(state: State, spawnY: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform, {
    posX: 0,
    posY: spawnY,
    posZ: 0,
  });
  state.addComponent(eid, Rigidbody);
  gateEntity(state, eid, { yFallback: spawnY });
  return eid;
}

/** No-arg shim around SpawnGateSystem.update so tests read like a frame tick. */
function tick(state: State): void {
  SpawnGateSystem.update!(state);
}

describe('SpawnGateSystem — terrain-ready latch', () => {
  let state: State;
  const terrainEid = 1000;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
  });

  it('keeps the entity frozen at its spawn Y while the heightmap is decoding', () => {
    // Terrain registered but heightmap not yet decoded (sampler.data === null)
    // and Rapier heightfield not built (collisionReady === false).
    getTerrainContext(state).set(terrainEid, makeTerrainField());

    const hero = makeGatedHero(state, 50);

    tick(state);

    expect(SpawnGateComponent.ready[hero]).toBe(0);
    expect(Transform.posY[hero]).toBe(50);
    // Gated entities must not accumulate fall velocity while frozen.
    expect(Rigidbody.velY[hero]).toBe(0);
  });

  it('snaps the entity to the ground once the heightmap is decoded AND the heightfield is built', () => {
    getTerrainContext(state).set(terrainEid, makeTerrainField());
    const hero = makeGatedHero(state, 50);

    tick(state);
    expect(SpawnGateComponent.ready[hero]).toBe(0);

    // Decode the heightmap (flat sea-level field) + flag collision ready.
    const field = getTerrainContext(state).get(terrainEid)!;
    field.sampler = flatSampler(new Float32Array([0]));
    field.initialized = true;
    field.collisionReady = true;

    tick(state);

    const expectedGround = getTerrainHeightAt(state, 0, 0);
    const expectedBvh = getBvhSurfaceHeight(state, 0, 50, 0);
    const ground = expectedBvh ?? expectedGround;
    expect(ground).toBe(0);
    expect(SpawnGateComponent.ready[hero]).toBe(1);
    expect(Transform.posY[hero]).toBeCloseTo(
      ground + SpawnGateComponent.skinDistance[hero],
      5
    );
  });

  it('latches: once ready, a later frame does not re-freeze the entity', () => {
    getTerrainContext(state).set(terrainEid, makeTerrainField());
    const hero = makeGatedHero(state, 50);

    const field = getTerrainContext(state).get(terrainEid)!;
    field.sampler = flatSampler(new Float32Array([0]));
    field.initialized = true;
    field.collisionReady = true;

    tick(state);
    expect(SpawnGateComponent.ready[hero]).toBe(1);
    const snappedY = Transform.posY[hero];

    // Simulate the game moving the entity after release; the gate must NOT
    // fight back and re-freeze it at the spawn Y.
    Transform.posY[hero] = snappedY + 3;
    Transform.dirty[hero] = 1;

    tick(state);

    expect(SpawnGateComponent.ready[hero]).toBe(1);
    expect(Transform.posY[hero]).toBeCloseTo(snappedY + 3, 5);
  });
});

describe('SpawnGateSystem — physics heightfield gate', () => {
  let state: State;
  const terrainEid = 1000;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
  });

  it('holds the gate open when the heightmap is decoded but the Rapier heightfield is still absent', () => {
    // Heightmap decoded + terrain initialized, but collisionReady === false:
    // the one-sided Rapier heightfield is not built yet, so releasing now
    // would let gravity tunnel the capsule through the floor.
    getTerrainContext(state).set(
      terrainEid,
      makeTerrainField({
        sampler: flatSampler(new Float32Array([0])),
        initialized: true,
        collisionReady: false,
      })
    );
    const hero = makeGatedHero(state, 50);

    tick(state);

    expect(SpawnGateComponent.ready[hero]).toBe(0);
    expect(Transform.posY[hero]).toBe(50);

    // Build the heightfield collider → gate can now release.
    const field = getTerrainContext(state).get(terrainEid)!;
    field.collisionReady = true;

    tick(state);

    expect(SpawnGateComponent.ready[hero]).toBe(1);
    expect(Transform.posY[hero]).toBeCloseTo(
      SpawnGateComponent.skinDistance[hero],
      5
    );
  });

  it('does not gate entities when no terrain field exists at all', () => {
    // No terrain in the scene → the gate has nothing to wait on, so the entity
    // snaps on the first tick using whatever surface query is available.
    const hero = makeGatedHero(state, 50);

    tick(state);

    expect(SpawnGateComponent.ready[hero]).toBe(1);
    expect(Transform.posY[hero]).toBeCloseTo(
      SpawnGateComponent.skinDistance[hero],
      5
    );
  });
});
