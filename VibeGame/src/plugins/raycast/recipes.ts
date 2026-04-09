import type { Recipe } from '../../core';

export const raycastSourceRecipe: Recipe = {
  name: 'raycast-source',
  components: ['transform', 'raycastSource', 'raycastResult'],
};
