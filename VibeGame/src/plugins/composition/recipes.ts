import type { Recipe } from '../../core';

export const compositionRecipe: Recipe = {
  name: 'Composition',
  components: ['transform', 'compositionPending'],
  parserOwnsChildren: true,
  parserAttributes: ['place', 'body', 'collider', 'collider-mode'],
};
