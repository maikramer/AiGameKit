import { describe, expect, it, beforeEach } from 'bun:test';
import {
  State,
  Transform,
  Rigidbody,
  getTerrainContext,
} from 'aigamekit-vibegame';
import { TerrainChunk } from '../../../src/plugins/terrain/components';
import { getPhysicsContext } from '../../../src/plugins/physics/systems';
import {
  CharacterController,
  CharacterMovement,
} from '../../../src/plugins/physics/components';
import {
  getGroundHeight,
  bumpGroundRevision,
} from '../../../src/plugins/terrain';
import type { TerrainEntityData } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  SpawnGateComponent,
  CharacterUnburySystem,
  gateEntity,
  resetUnburySurfaceCache,
} from '../../../src/plugins/spawn-gate';

/** Flat decoded heightmap: surface sits at fill × maxHeight everywhere. */
function flatSampler(fill = 0.5, maxHeight = 20): HeightSampler {
  return {
    width: 1,
    height: 1,
    data: new Float32Array([fill]),
    worldSize: 256,
    maxHeight,
  };
}

function makeTerrainField(
  overrides: Partial<TerrainEntityData> = {}
): TerrainEntityData {
  return {
    sampler: flatSampler(),
    chunks: new Set(),
    heightmapUrl: '/terrain/heightmap.png',
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

function makeCharacter(state: State, y: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform, { posX: 0, posY: y, posZ: 0 });
  state.addComponent(eid, Rigidbody);
  Rigidbody.posX[eid] = 0;
  Rigidbody.posY[eid] = y;
  Rigidbody.posZ[eid] = 0;
  state.addComponent(eid, CharacterController);
  state.addComponent(eid, CharacterMovement);
  CharacterMovement.velocityY[eid] = -90;
  return eid;
}

/**
 * Give the field one chunk heightfield covering the origin, plus a stub Rapier
 * world so `isTerrainColliderAt` can see it. Mirrors the collider ring the
 * terrain builds around the camera.
 */
function withChunkCollider(state: State, terrainEid: number): number {
  getPhysicsContext(state).physicsWorld = {} as never;
  const chunk = state.createEntity();
  state.addComponent(chunk, TerrainChunk);
  TerrainChunk.originX[chunk] = 0;
  TerrainChunk.originZ[chunk] = 0;
  TerrainChunk.size[chunk] = 64;
  const field = getTerrainContext(state).get(terrainEid)!;
  field.chunkColliders.set(chunk, {
    numColliders: () => 1,
  } as never);
  return chunk;
}

function tick(state: State): void {
  CharacterUnburySystem.update!(state);
}

describe('CharacterUnburySystem — buried character recovery', () => {
  let state: State;
  const terrainEid = 1000;
  let surfaceY: number;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
    state.registerComponent('character-controller', CharacterController);
    state.registerComponent('character-movement', CharacterMovement);
    state.registerComponent('terrain-chunk', TerrainChunk);
    getTerrainContext(state).set(terrainEid, makeTerrainField());
    surfaceY = getGroundHeight(state, 0, 0);
  });

  it('re-seats a character that fell below the terrain surface', () => {
    const hero = makeCharacter(state, surfaceY - 1271); // dump scenario

    tick(state);

    expect(Rigidbody.posY[hero]).toBeCloseTo(surfaceY + 0.05, 5);
    expect(CharacterMovement.velocityY[hero]).toBe(0);
    expect(CharacterController.grounded[hero]).toBe(1);
  });

  it('leaves characters at or above the surface alone', () => {
    const airborne = makeCharacter(state, surfaceY + 2);

    tick(state);

    expect(Rigidbody.posY[airborne]).toBeCloseTo(surfaceY + 2, 5);
    expect(CharacterMovement.velocityY[airborne]).toBe(-90);
  });

  it('does nothing without terrain fields (interiors/platform worlds)', () => {
    getTerrainContext(state).clear();
    const hero = makeCharacter(state, -100);

    tick(state);

    expect(Rigidbody.posY[hero]).toBe(-100);
    expect(CharacterMovement.velocityY[hero]).toBe(-90);
  });

  it('does nothing while a heightmap is still decoding', () => {
    const field = getTerrainContext(state).get(terrainEid)!;
    field.sampler = { ...flatSampler(), data: null };

    const hero = makeCharacter(state, surfaceY - 50);

    tick(state);

    expect(Rigidbody.posY[hero]).toBe(surfaceY - 50);
  });

  it('skips entities still held by the spawn gate', () => {
    const hero = makeCharacter(state, surfaceY - 50);
    state.addComponent(hero, SpawnGateComponent);
    gateEntity(state, hero, { yFallback: 40 });

    tick(state);

    expect(Rigidbody.posY[hero]).toBe(surfaceY - 50);
    expect(SpawnGateComponent.ready[hero]).toBe(0);
  });

  it('uses the gate skin distance for released entities', () => {
    const hero = makeCharacter(state, surfaceY - 5);
    gateEntity(state, hero, { yFallback: 40, skinDistance: 0.2 });
    SpawnGateComponent.ready[hero] = 1; // already latched

    tick(state);

    expect(Rigidbody.posY[hero]).toBeCloseTo(surfaceY + 0.2, 5);
  });

  it('carries a character with no chunk collider under it flush on the surface', () => {
    // Terrain collision is a ring around the camera: a creature outside it has
    // nothing to stand on, so the sampler carries it instead of letting gravity
    // sink it (the old 0.35 m sawtooth + one warning per creature per tick).
    const creature = makeCharacter(state, surfaceY - 0.1);

    tick(state);

    expect(Rigidbody.posY[creature]).toBeCloseTo(surfaceY + 0.05, 5);
    expect(CharacterController.grounded[creature]).toBe(1);
    expect(CharacterMovement.velocityY[creature]).toBe(0);
  });

  it('leaves a lightly sunk character to the CCT when a collider covers it', () => {
    withChunkCollider(state, terrainEid);
    const hero = makeCharacter(state, surfaceY - 0.1); // within GROUND_SNAP_MAX

    tick(state);

    expect(Rigidbody.posY[hero]).toBeCloseTo(surfaceY - 0.1, 4);
    expect(CharacterMovement.velocityY[hero]).toBe(-90);
  });

  it('still re-seats a character buried past the snap limit under a collider', () => {
    withChunkCollider(state, terrainEid);
    const hero = makeCharacter(state, surfaceY - 3);

    tick(state);

    expect(Rigidbody.posY[hero]).toBeCloseTo(surfaceY + 0.05, 5);
    expect(CharacterController.grounded[hero]).toBe(1);
  });
});

/**
 * Carrying an off-collider roster is the steady state, so the lattice probe
 * behind `getGroundHeight` (a five-point Catmull-Rom cross) must not be paid
 * per character per fixed step. The cache is observed through its effect: a
 * silent sampler edit stays invisible until something invalidates the entry.
 */
describe('CharacterUnburySystem — surface sample cache', () => {
  const MAX_HEIGHT = 20;

  /**
   * Uniform 2x2 heightmap. `sampleNormalized` bails to 0 on a 1x1 grid, so the
   * flat stand-in above cannot express a non-zero surface.
   */
  function uniformSampler(fill: number): HeightSampler {
    return {
      width: 2,
      height: 2,
      data: new Float32Array([fill, fill, fill, fill]),
      worldSize: 256,
      maxHeight: MAX_HEIGHT,
    };
  }

  let state: State;
  const terrainEid = 1000;
  let surfaceY: number;

  /** Rewrite the field's heights in place, as a carve/reload would. */
  function setSurface(fill: number): number {
    const { data } = getTerrainContext(state).get(terrainEid)!.sampler;
    data!.fill(fill);
    return fill * MAX_HEIGHT;
  }

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('rigidbody', Rigidbody);
    state.registerComponent('character-controller', CharacterController);
    state.registerComponent('character-movement', CharacterMovement);
    state.registerComponent('terrain-chunk', TerrainChunk);
    getTerrainContext(state).set(
      terrainEid,
      makeTerrainField({ sampler: uniformSampler(0.5) })
    );
    surfaceY = getGroundHeight(state, 0, 0);
    expect(surfaceY).toBeCloseTo(10, 5);
    resetUnburySurfaceCache(state);
  });

  it('reuses the sampled height while the character stays put', () => {
    const creature = makeCharacter(state, surfaceY - 0.5);
    tick(state);
    expect(Rigidbody.posY[creature]).toBeCloseTo(surfaceY + 0.05, 5);

    setSurface(0.75);

    // Gravity pulls it off the surface every step and it is re-pinned each
    // time — from the memoised height, so the silent edit is not picked up.
    for (let i = 0; i < 20; i++) {
      Rigidbody.posY[creature] -= 0.02;
      tick(state);
    }

    expect(Rigidbody.posY[creature]).toBeCloseTo(surfaceY + 0.05, 5);
  });

  it('re-probes once the character has moved horizontally', () => {
    const creature = makeCharacter(state, surfaceY - 0.5);
    tick(state);

    const movedY = setSurface(0.75);

    Rigidbody.posX[creature] = 0.01; // inside the epsilon — still cached
    Rigidbody.posY[creature] -= 0.5;
    tick(state);
    expect(Rigidbody.posY[creature]).toBeCloseTo(surfaceY + 0.05, 5);

    Rigidbody.posX[creature] = 5;
    Rigidbody.posY[creature] -= 0.5;
    tick(state);
    expect(Rigidbody.posY[creature]).toBeCloseTo(movedY + 0.05, 5);
  });

  it('re-probes after the ground itself is carved or reloaded', () => {
    const creature = makeCharacter(state, surfaceY - 0.5);
    tick(state);

    const movedY = setSurface(0.75);
    bumpGroundRevision(state);
    Rigidbody.posY[creature] -= 0.5;
    tick(state);

    expect(Rigidbody.posY[creature]).toBeCloseTo(movedY + 0.05, 5);
  });

  it('keeps the caches of separate worlds apart', () => {
    const creature = makeCharacter(state, surfaceY - 0.5);
    tick(state);

    const other = new State();
    other.registerComponent('transform', Transform);
    other.registerComponent('rigidbody', Rigidbody);
    other.registerComponent('character-controller', CharacterController);
    other.registerComponent('character-movement', CharacterMovement);
    other.registerComponent('terrain-chunk', TerrainChunk);
    getTerrainContext(other).set(
      terrainEid,
      makeTerrainField({ sampler: uniformSampler(0.75) })
    );

    // Same entity id in a world with a different surface: no cross-talk.
    const twin = makeCharacter(other, surfaceY - 0.5);
    expect(twin).toBe(creature);

    tick(other);

    expect(Rigidbody.posY[twin]).toBeCloseTo(0.75 * MAX_HEIGHT + 0.05, 5);
  });
});
