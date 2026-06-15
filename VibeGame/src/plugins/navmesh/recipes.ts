import type { Recipe } from '../../core';

export const navMeshRecipe: Recipe = {
  name: 'NavMesh',
  components: ['nav-mesh-surface'],
};

export const navMeshWalkableRecipe: Recipe = {
  name: 'NavMeshWalkable',
  merge: true,
  components: ['nav-mesh-walkable'],
};

export const navMeshAgentRecipe: Recipe = {
  name: 'NavMeshAgent',
  merge: true,
  components: ['nav-mesh-agent'],
};
