import type { Recipe } from '../../core';

export const orbitCameraRecipe: Recipe = {
  name: 'OrbitCamera',
  components: ['orbit-camera', 'transform', 'main-camera'],
  merge: true,
};
