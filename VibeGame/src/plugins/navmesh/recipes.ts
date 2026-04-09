import type { Recipe } from '../../core';

export const navMeshRecipe: Recipe = {
  name: 'nav-mesh',
  components: ['navMesh'],
};

export const navAgentRecipe: Recipe = {
  name: 'nav-agent',
  components: ['transform', 'navAgent'],
};
