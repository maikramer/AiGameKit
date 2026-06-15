import type { Recipe } from '../../core';

const SPAWNER_PARSER_ATTRIBUTES = [
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
  'avoid-overlaps',
  'footprint-radius',
  'max-distance',
  'instanced',
];

/** `<SpawnGroup>` — back-compat alias, behaves as a static spawner. */
export const spawnGroupRecipe: Recipe = {
  name: 'SpawnGroup',
  components: ['transform', 'spawnerPending'],
  parserOwnsChildren: true,
  parserAttributes: SPAWNER_PARSER_ATTRIBUTES,
};

/** `<StaticSpawner>` — repeated immobile props, rendered instanced (with LOD). */
export const staticSpawnerRecipe: Recipe = {
  name: 'StaticSpawner',
  components: ['transform', 'spawnerPending'],
  parserOwnsChildren: true,
  parserAttributes: SPAWNER_PARSER_ATTRIBUTES,
};

/** `<DynamicSpawner>` — moving scripted entities (enemies/NPCs), not instanced. */
export const dynamicSpawnerRecipe: Recipe = {
  name: 'DynamicSpawner',
  components: ['transform', 'spawnerPending'],
  parserOwnsChildren: true,
  parserAttributes: SPAWNER_PARSER_ATTRIBUTES,
};

/** Explicit no-spawn disc: `<SpawnExclusion at="16 8" radius="7">`. */
export const spawnExclusionRecipe: Recipe = {
  name: 'SpawnExclusion',
  components: ['spawn-exclusion'],
};

/** Overrides core `entity` with transform + optional terrain placement (`place` attr). */
export const entitySpawnerRecipe: Recipe = {
  name: 'GameObject',
  components: ['transform'],
  parserAttributes: ['place'],
};
