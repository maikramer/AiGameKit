import type { Recipe } from '../../core';

export const rendererRecipe: Recipe = {
  name: 'MeshRenderer',
  components: ['transform', 'meshRenderer'],
};

export const pointLightRecipe: Recipe = {
  name: 'PointLight',
  components: ['transform', 'pointLight'],
};

export const spotLightRecipe: Recipe = {
  name: 'SpotLight',
  components: ['transform', 'spotLight'],
};
