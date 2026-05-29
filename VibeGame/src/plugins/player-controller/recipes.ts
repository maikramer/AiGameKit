import type { Recipe } from '../../core';

export const thirdPersonCameraRecipe: Recipe = {
  name: 'ThirdPersonCamera',
  components: ['third-person-camera', 'transform', 'main-camera'],
  merge: true,
};
