import { describe, expect, it } from 'bun:test';
import { defineQuery, State } from 'aigamekit-vibegame';
import { Transform } from '../../../src/plugins/transforms/components';
import {
  PlacePending,
  SpawnerPending,
  TerrainSpawned,
} from '../../../src/plugins/spawner/components';
import { SpawnVariation } from '../../../src/plugins/spawn-variation/components';
import { SpawnExclusion } from '../../../src/plugins/spawner/occupancy';
import { setSpawnGroupSpec } from '../../../src/plugins/spawner/context';
import { TerrainSpawnSystem } from '../../../src/plugins/spawner/systems';
import type { SpawnGroupSpec } from '../../../src/plugins/spawner/types';
import { getVariationPreset } from '../../../src/plugins/spawn-variation';
import { Terrain } from '../../../src/plugins/terrain/components';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

/**
 * TerrainSpawnSystem contract for planner-supplied explicit points
 * (`SpawnGroupSpec.points`, emitted by `<NatureSpawner>`): the count comes
 * from the points themselves, each instance is validated exactly at its
 * point (no XZ re-sampling) and a failed slope validation drops the
 * instance instead of moving it.
 */

const WORLD = 200;
const FLAT_H = 10;

function buildState(options?: { stepped?: boolean }): State {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('spawnerPending', SpawnerPending);
  state.registerComponent('placePending', PlacePending);
  state.registerComponent('terrainSpawned', TerrainSpawned);
  state.registerComponent('spawnVariation', SpawnVariation);
  state.registerComponent('spawn-exclusion', SpawnExclusion);
  state.registerComponent('terrain', Terrain);

  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.resolution[terrain] = 64;
  Terrain.levels[terrain] = 4;

  const width = 65;
  const height = 65;
  const sampler: HeightSampler = {
    width,
    height,
    data: new Float32Array(width * height),
    worldSize: WORLD,
    maxHeight: 100,
  };
  for (let z = 0; z < height; z++) {
    for (let x = 0; x < width; x++) {
      const lz = (z / (height - 1) - 0.5) * WORLD;
      const h = options?.stepped && Math.abs(lz) < 8 ? 90 : FLAT_H;
      sampler.data![z * width + x] = h / 100;
    }
  }

  getTerrainContext(state).set(terrain, {
    initialized: true,
    worldOffset: { x: 0, z: 0 },
    sampler,
    density: undefined,
  } as never);
  return state;
}

function pointsSpec(
  points: Array<[number, number]>,
  overrides?: Partial<SpawnGroupSpec>
): SpawnGroupSpec {
  return {
    mode: 'static',
    spawnGroupProfile: 'none',
    spawnCountMode: 'fixed',
    // Deliberately wrong: points override the count mode.
    count: 99,
    densityPerKm2: 0,
    countRangeMin: 0,
    countRangeMax: 0,
    seed: 5,
    regionMin: [-100, 0, -100],
    regionMax: [100, 0, 100],
    alignToTerrain: false,
    baseYOffset: 0,
    groundAlign: 'none',
    randomYaw: false,
    scaleDistribution: 'linear',
    scaleDiscreteValues: [],
    scaleMin: 1,
    scaleMax: 1,
    scaleAxisMin: 1,
    scaleAxisMax: 1,
    yawDistribution: 'linear',
    yawDiscreteDeg: [],
    surfaceEpsilon: 0.75,
    surfaceEpsilonAuto: false,
    maxSlopeDeg: 45,
    maxSlopePlacementAttempts: 32,
    pickStrategy: 'random',
    avoidWater: false,
    avoidRoad: false,
    inWater: false,
    nearWater: false,
    avoidOverlaps: true,
    footprintRadius: 1,
    maxDistance: 0,
    instanced: true,
    clusterCount: 0,
    clusterRadius: 0,
    variation: getVariationPreset('none'),
    templates: [
      {
        tagName: 'GameObject',
        attributes: { url: '/unit/prop.glb', instanced: 'true' },
        role: 'visual',
      },
    ],
    points,
    ...overrides,
  };
}

function runSpawner(state: State, spec: SpawnGroupSpec): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  state.addComponent(eid, SpawnerPending);
  SpawnerPending.spawned[eid] = 0;
  setSpawnGroupSpec(state, eid, spec);
  TerrainSpawnSystem.update!(state);
  expect(SpawnerPending.spawned[eid]).toBe(1);
  return eid;
}

const spawnedQuery = defineQuery([TerrainSpawned]);

function spawnedPositions(state: State): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const e of spawnedQuery(state.world)) {
    out.push([Transform.posX[e], Transform.posZ[e]]);
  }
  return out;
}

describe('TerrainSpawnSystem explicit points mode', () => {
  it('spawns exactly one instance per point at its exact XZ', () => {
    const state = buildState();
    const points: Array<[number, number]> = [
      [10, 10],
      [30, -20],
      [-15, 5],
    ];
    runSpawner(state, pointsSpec(points));

    const positions = spawnedPositions(state);
    expect(positions.length).toBe(3);
    expect(positions).toEqual(points);
    for (const e of spawnedQuery(state.world)) {
      expect(Transform.posY[e]).toBeCloseTo(FLAT_H, 5);
    }
  });

  it('overrides the count mode (99 becomes 3 instances)', () => {
    const state = buildState();
    runSpawner(
      state,
      pointsSpec([
        [0, 0],
        [4, 4],
        [-4, -4],
      ])
    );
    expect(spawnedPositions(state).length).toBe(3);
  });

  it('drops a point that fails the slope validation instead of moving it', () => {
    const state = buildState({ stepped: true });
    // Flat plateau point passes; the step wall (90 → 10 over ~3 m) fails a
    // 5° limit. No re-sampling means the dropped instance just never exists.
    runSpawner(
      state,
      pointsSpec(
        [
          [50, 50],
          [0, 8],
        ],
        { maxSlopeDeg: 5 }
      )
    );

    const positions = spawnedPositions(state);
    expect(positions.length).toBe(1);
    expect(positions[0]).toEqual([50, 50]);
  });

  it('honours a huge maxSlopeDeg on the same wall (accept-any slope)', () => {
    const state = buildState({ stepped: true });
    runSpawner(
      state,
      pointsSpec(
        [
          [50, 50],
          [0, 8],
        ],
        { maxSlopeDeg: 90 }
      )
    );
    expect(spawnedPositions(state).length).toBe(2);
  });
});
