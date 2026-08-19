import type { Plugin, Recipe } from '../../core';
import { Nature } from './components';
import { natureSpawnerParser } from './parser';
import { NaturePlannerSystem } from './planner-system';

/**
 * `<NatureSpawner>` — rule-driven composite scatter. Declares species
 * (`<Species>` + `<Where>` site conditions) and mixed groves (`<Grove>` +
 * `<Member>`); the planner evaluates the post-carve terrain and emits one
 * explicit-point SpawnGroupSpec per species.
 */
export const natureSpawnerRecipe: Recipe = {
  name: 'NatureSpawner',
  components: ['transform', 'spawnerPending', 'nature'],
  parserOwnsChildren: true,
  parserAttributes: [
    'seed',
    'region-min',
    'region-max',
    'count',
    'density-per-km2',
    'min-spacing',
    'noise-scale',
  ],
};

export const NaturePlugin: Plugin = {
  recipes: [natureSpawnerRecipe],
  systems: [NaturePlannerSystem],
  components: {
    nature: Nature,
  },
  config: {
    parsers: {
      NatureSpawner: natureSpawnerParser,
    },
    defaults: {
      nature: {
        planned: 0,
      },
      spawnerPending: {
        spawned: 0,
      },
    },
  },
};
