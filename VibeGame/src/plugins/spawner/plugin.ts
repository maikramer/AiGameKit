import type { Plugin } from '../../core';
import { SpawnerPending } from './components';
import { spawnGroupParser } from './parser';
import { spawnGroupRecipe } from './recipes';
import { TerrainSpawnSystem } from './systems';

export const SpawnerPlugin: Plugin = {
  recipes: [spawnGroupRecipe],
  systems: [TerrainSpawnSystem],
  components: {
    spawnerPending: SpawnerPending,
  },
  config: {
    parsers: {
      'spawn-group': spawnGroupParser,
    },
    defaults: {
      spawnerPending: {
        spawned: 0,
      },
    },
  },
};
