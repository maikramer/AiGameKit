import type { Recipe } from '../../core';

export const terrainRecipe: Recipe = {
  name: 'Terrain',
  components: ['terrain', 'transform'],
  merge: true,
};

/**
 * `<TerrainPad at="0 0" size="60 60" falloff="12" corner-radius="10">` —
 * levels a settlement pad into the terrain so buildings sit flush instead of
 * floating over rugged ground. `height` (optional) pins the pad elevation;
 * omitted, it uses the terrain height sampled at the pad centre.
 */
export const terrainPadRecipe: Recipe = {
  name: 'TerrainPad',
  components: ['transform', 'terrain-pad'],
  parserAttributes: ['at', 'size'],
};
