import type { Plugin } from '../../core';
import { PlacePending, SpawnerPending, TerrainSpawned } from './components';
import { entityParser } from './entity-parser';
import { entitySpawnerRecipe, spawnGroupRecipe } from './recipes';
import { spawnGroupParser } from './parser';
import { TerrainPlaceSystem } from './place-system';
import { TerrainSpawnSystem, VegetationUpdateSystem } from './systems';
import { SpawnReadyGateSystem } from './ready-gate';

export const SpawnerPlugin: Plugin = {
  recipes: [spawnGroupRecipe, entitySpawnerRecipe],
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
    },
  },
};
