import type { Plugin, State } from '../../core';
import { NavMeshAgent, NavMeshSurface } from './components';
import { navAgentRecipe, navMeshRecipe } from './recipes';
import {
  NavAgentMoveSystem,
  NavAgentPathSystem,
  NavMeshBuildSystem,
  NavMeshLoadSystem,
} from './systems';

function targetAdapter(entity: number, value: string, _state: State): void {
  const parts = value.trim().split(/\s+/).map(Number);
  NavMeshAgent.targetX[entity] = parts[0] ?? 0;
  NavMeshAgent.targetY[entity] = parts[1] ?? 0;
  NavMeshAgent.targetZ[entity] = parts[2] ?? 0;
}

export const NavmeshPlugin: Plugin = {
  systems: [
    NavMeshLoadSystem,
    NavMeshBuildSystem,
    NavAgentPathSystem,
    NavAgentMoveSystem,
  ],
  recipes: [navMeshRecipe, navAgentRecipe],
  components: {
    navMesh: NavMeshSurface,
    navAgent: NavMeshAgent,
  },
  config: {
    defaults: {
      navMesh: {
        loaded: 0,
        buildFromScene: 0,
      },
      navAgent: {
        targetX: 0,
        targetY: 0,
        targetZ: 0,
        speed: 3,
        tolerance: 0.35,
        status: 0,
      },
    },
    adapters: {
      'nav-agent': {
        target: targetAdapter,
      },
    },
  },
};
