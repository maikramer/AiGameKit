import type { Recipe } from '../../core';

export const gltfLoadRecipe: Recipe = {
  name: 'gltf-load',
  components: ['transform', 'gltfPending'],
  /** Metadado de templates (spawn-group); não mapeia para componentes. */
  parserAttributes: ['role', 'profile'],
};

export const gltfDynamicRecipe: Recipe = {
  name: 'gltf-dynamic',
  components: ['transform', 'gltfPending', 'gltfPhysicsPending'],
  parserAttributes: ['role', 'profile'],
};
