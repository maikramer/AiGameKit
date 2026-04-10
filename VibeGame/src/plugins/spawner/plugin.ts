import type { Plugin } from '../../core';
import { PlacePending, SpawnerPending } from './components';
import { entityParser } from './entity-parser';
import { entitySpawnerRecipe, spawnGroupRecipe } from './recipes';
import { spawnGroupParser } from './parser';
import { TerrainPlaceSystem } from './place-system';
import { TerrainSpawnSystem } from './systems';

export const SpawnerPlugin: Plugin = {
  recipes: [spawnGroupRecipe, entitySpawnerRecipe],
  systems: [TerrainSpawnSystem, TerrainPlaceSystem],
  components: {
    spawnerPending: SpawnerPending,
    placePending: PlacePending,
  },
  config: {
    parsers: {
      'spawn-group': spawnGroupParser,
      entity: entityParser,
    },
    defaults: {
      spawnerPending: {
        spawned: 0,
      },
      placePending: {
        spawned: 0,
      },
    },
  },
};
