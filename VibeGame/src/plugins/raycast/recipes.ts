import type { Recipe } from '../../core';

export const raycastSourceRecipe: Recipe = {
  name: 'RaycastSource',
  components: ['transform', 'raycastSource', 'raycastHit'],
  merge: true,
};
