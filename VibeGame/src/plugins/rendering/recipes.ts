import type { Recipe } from '../../core';

export const rendererRecipe: Recipe = {
  name: 'MeshRenderer',
  merge: true,
  components: ['transform', 'meshRenderer'],
};

export const pointLightRecipe: Recipe = {
  name: 'PointLight',
  merge: true,
  components: ['transform', 'pointLight'],
};

export const spotLightRecipe: Recipe = {
  name: 'SpotLight',
  merge: true,
  components: ['transform', 'spotLight'],
};
