import type { Recipe } from '../../core';

export const spawnGroupRecipe: Recipe = {
  name: 'SpawnGroup',
  components: ['transform', 'spawnerPending'],
  parserOwnsChildren: true,
  parserAttributes: [
    'profile',
    'count',
    'count-min',
    'count-max',
    'density-per-km2',
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
    'scale-distribution',
    'scale-discrete',
    'yaw-distribution',
    'yaw-discrete-deg',
    'yaw-step-deg',
    'surface-epsilon',
    'surface-epsilon-auto',
    'max-slope-deg',
    'max-slope-attempts',
    'avoid-water',
    'max-distance',
  ],
};

/** Overrides core `entity` with transform + optional terrain placement (`place` attr). */
export const entitySpawnerRecipe: Recipe = {
  name: 'GameObject',
  components: ['transform'],
  parserAttributes: ['place'],
};
