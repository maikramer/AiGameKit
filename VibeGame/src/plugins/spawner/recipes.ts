import type { Recipe } from '../../core';

export const spawnGroupRecipe: Recipe = {
  name: 'spawn-group',
  components: ['transform', 'spawnerPending'],
  parserOwnsChildren: true,
  parserAttributes: [
    'profile',
    'count',
    'seed',
    'region-min',
    'region-max',
    'pick-strategy',
    'align-to-terrain',
    'base-y-offset',
    'ground-align',
    'random-yaw',
    'scale-min',
    'scale-max',
    'surface-epsilon',
    'max-slope-deg',
    'max-slope-attempts',
  ],
};

/** Overrides core `entity` with transform + optional terrain placement (`place` attr). */
export const entitySpawnerRecipe: Recipe = {
  name: 'entity',
  components: ['transform'],
  parserAttributes: ['place'],
};
