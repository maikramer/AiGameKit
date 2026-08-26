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
 *
 * Terraces are stacks of these with an explicit `height` and a falloff at the
 * sampler's texel floor — the transition is then as steep as the heightfield
 * can represent, which on a 0.5 m/texel field makes a 2 m step ~63-76° and so
 * unwalkable for the character controller. Vertical cliffs are not
 * representable: everything downstream (chunk mesh, Rapier heightfield, BVH) is
 * a height field.
 */
export const terrainPadRecipe: Recipe = {
  name: 'TerrainPad',
  components: ['transform', 'terrain-pad'],
  parserAttributes: ['at', 'size'],
};
