import type { Adapter, Plugin } from '../../core';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { entityParser } from './entity-parser';
import {
  dynamicSpawnerRecipe,
  entitySpawnerRecipe,
  spawnExclusionRecipe,
  spawnGroupRecipe,
  staticSpawnerRecipe,
} from './recipes';
import { spawnGroupParser } from './parser';
import { SpawnExclusion } from './occupancy';
import { TerrainPlaceSystem } from './place-system';
import { TerrainSpawnSystem } from './systems';
import { SpawnReadyGateSystem } from './ready-gate';

export const SpawnerPlugin: Plugin = {
  recipes: [
    spawnGroupRecipe,
    staticSpawnerRecipe,
    dynamicSpawnerRecipe,
    entitySpawnerRecipe,
    spawnExclusionRecipe,
  ],
  systems: [TerrainSpawnSystem, TerrainPlaceSystem, SpawnReadyGateSystem],
  components: {
    spawnerPending: SpawnerPending,
    placePending: PlacePending,
    terrainSpawned: TerrainSpawned,
    'spawn-exclusion': SpawnExclusion,
  },
  config: {
    parsers: {
      SpawnGroup: spawnGroupParser,
      StaticSpawner: spawnGroupParser,
      DynamicSpawner: spawnGroupParser,
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
        radius: ((entity, value) => {
          const n = parseFloat(String(value));
          if (!Number.isNaN(n) && n > 0) {
            SpawnExclusion.radius[entity] = n;
          }
        }) as Adapter,
      },
    },
  },
};
