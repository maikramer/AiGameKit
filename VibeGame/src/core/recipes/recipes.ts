import type { Recipe } from '../';

export const entityRecipe: Recipe = {
  name: 'GameObject',
  components: ['transform'],
};

export const transformRecipe: Recipe = {
  name: 'Transform',
  merge: true,
  components: ['transform'],
};
