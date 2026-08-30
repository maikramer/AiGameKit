import { describe, expect, it, beforeEach } from 'bun:test';
import { State, Transform, Rigidbody, getTerrainContext } from 'aigamekit-vibegame';
import { TerrainChunk } from '../../../src/plugins/terrain/components';
import { getPhysicsContext } from '../../../src/plugins/physics/systems';
import {
  CharacterController,
  CharacterMovement,
} from '../../../src/plugins/physics/components';
import { getGroundHeight } from '../../../src/plugins/terrain';
import type { TerrainEntityData } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import {
  SpawnGateComponent,
  CharacterUnburySystem,
  gateEntity,
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
