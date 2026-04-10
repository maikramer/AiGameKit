import type { Recipe } from '../../core';

export const rendererRecipe: Recipe = {
  name: 'renderer',
  components: ['transform', 'renderer'],
};

export const pointLightRecipe: Recipe = {
  name: 'point-light',
  components: ['transform', 'pointLight'],
};

export const spotLightRecipe: Recipe = {
  name: 'spot-light',
  components: ['transform', 'spotLight'],
};
