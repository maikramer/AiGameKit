import type { Recipe } from '../../core';

export const terrainRecipe: Recipe = {
  name: 'Terrain',
  components: ['terrain', 'transform'],
  merge: true,
};
