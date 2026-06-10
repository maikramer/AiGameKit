import type { Adapter, Plugin } from '../../core';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { entityParser } from './entity-parser';
import {
  entitySpawnerRecipe,
  spawnExclusionRecipe,
  spawnGroupRecipe,
} from './recipes';
import { spawnGroupParser } from './parser';
import { SpawnExclusion } from './occupancy';
import { TerrainPlaceSystem } from './place-system';
import { TerrainSpawnSystem, VegetationUpdateSystem } from './systems';
import { SpawnReadyGateSystem } from './ready-gate';

export const SpawnerPlugin: Plugin = {
  recipes: [spawnGroupRecipe, entitySpawnerRecipe, spawnExclusionRecipe],
  systems: [
    TerrainSpawnSystem,
    TerrainPlaceSystem,
    VegetationUpdateSystem,
    SpawnReadyGateSystem,
  ],
  components: {
    spawnerPending: SpawnerPending,
    placePending: PlacePending,
    terrainSpawned: TerrainSpawned,
    'spawn-exclusion': SpawnExclusion,
  },
  config: {
    parsers: {
      SpawnGroup: spawnGroupParser,
      GameObject: entityParser,
    },
    defaults: {
      spawnerPending: {
        spawned: 0,
      },
      placePending: {
        spawned: 0,
      },
      terrainSpawned: {
        yOffset: 0,
        surfaceEpsilon: 0.75,
      },
      'spawn-exclusion': {
        x: 0,
        z: 0,
        radius: 5,
        registered: 0,
      },
    },
    adapters: {
      'spawn-exclusion': {
        // `at: x z` — two world coordinates in one attribute.
        at: ((entity, value) => {
          const parts = String(value)
            .trim()
            .split(/\s+/)
            .map((v) => parseFloat(v));
          if (parts.length >= 2 && parts.every((n) => !Number.isNaN(n))) {
            SpawnExclusion.x[entity] = parts[0]!;
            SpawnExclusion.z[entity] = parts[1]!;
          }
        }) as Adapter,
      },
    },
  },
};
