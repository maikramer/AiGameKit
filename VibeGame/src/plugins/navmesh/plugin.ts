import type { Plugin } from '../../core';
import { NavMeshAgent, NavMeshSurface, NavMeshWalkable } from './components';
import {
  navMeshAgentRecipe,
  navMeshRecipe,
  navMeshWalkableRecipe,
} from './recipes';
import { NavMeshAgentSystem, NavMeshInitSystem } from './systems';

export const NavMeshPlugin: Plugin = {
  systems: [NavMeshInitSystem, NavMeshAgentSystem],
  recipes: [navMeshRecipe, navMeshWalkableRecipe, navMeshAgentRecipe],
  components: {
    'nav-mesh-surface': NavMeshSurface,
    'nav-mesh-walkable': NavMeshWalkable,
    'nav-mesh-agent': NavMeshAgent,
  },
  config: {
    defaults: {
      'nav-mesh-surface': {
        enabled: 1,
        generated: 0,
      },
      'nav-mesh-walkable': {
        enabled: 1,
      },
      'nav-mesh-agent': {
        agentIndex: -1,
        speed: 3,
        radius: 0.4,
        height: 1.0,
        targetX: 0,
        targetY: 0,
        targetZ: 0,
        hasTarget: 0,
        enabled: 1,
      },
    },
  },
};
