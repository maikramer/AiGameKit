import type { Recipe } from '../../core';

export const gltfLoadRecipe: Recipe = {
  name: 'gltf-load',
  components: ['transform', 'gltfPending'],
};

export const gltfDynamicRecipe: Recipe = {
  name: 'gltf-dynamic',
  components: ['transform', 'gltfPending', 'gltfPhysicsPending'],
};
