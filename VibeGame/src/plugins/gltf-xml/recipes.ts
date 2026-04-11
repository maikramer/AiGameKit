import type { Recipe } from '../../core';

export const gltfLoadRecipe: Recipe = {
  name: 'GLTFLoader',
  components: ['transform', 'gltfPending'],
  /** Metadado de templates (spawn-group); não mapeia para componentes. */
  parserAttributes: ['role', 'profile'],
};

export const gltfDynamicRecipe: Recipe = {
  name: 'GLTFDynamic',
  components: ['transform', 'gltfPending', 'gltfPhysicsPending'],
  parserAttributes: ['role', 'profile'],
};
