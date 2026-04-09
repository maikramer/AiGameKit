import type { Recipe } from '../../core';

export const spawnGroupRecipe: Recipe = {
  name: 'spawn-group',
  components: ['transform', 'spawnerPending'],
};

export const placeRecipe: Recipe = {
  name: 'place',
  components: ['transform', 'placePending'],
};
