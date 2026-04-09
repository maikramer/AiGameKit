import type { Plugin } from '../../core';
import { PlacePending, SpawnerPending } from './components';
import { placeParser } from './place-parser';
import { placeRecipe, spawnGroupRecipe } from './recipes';
import { spawnGroupParser } from './parser';
import { TerrainPlaceSystem } from './place-system';
import { TerrainSpawnSystem } from './systems';

export const SpawnerPlugin: Plugin = {
  recipes: [spawnGroupRecipe, placeRecipe],
  systems: [TerrainSpawnSystem, TerrainPlaceSystem],
  components: {
    spawnerPending: SpawnerPending,
    placePending: PlacePending,
  },
  config: {
    parsers: {
      'spawn-group': spawnGroupParser,
      place: placeParser,
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
