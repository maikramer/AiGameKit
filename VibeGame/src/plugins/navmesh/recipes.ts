import type { Recipe } from '../../core';

export const navMeshRecipe: Recipe = {
  name: 'NavMeshSurface',
  components: ['navMeshSurface'],
};

export const navAgentRecipe: Recipe = {
  name: 'NavMeshAgent',
  components: ['transform', 'navMeshAgent'],
};
