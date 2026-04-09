import type { Plugin, State } from '../../core';
import { NavAgent, NavMesh } from './components';
import { navAgentRecipe, navMeshRecipe } from './recipes';
import {
  NavAgentMoveSystem,
  NavAgentPathSystem,
  NavMeshBuildSystem,
  NavMeshLoadSystem,
} from './systems';

function targetAdapter(entity: number, value: string, _state: State): void {
  const parts = value.trim().split(/\s+/).map(Number);
  NavAgent.targetX[entity] = parts[0] ?? 0;
  NavAgent.targetY[entity] = parts[1] ?? 0;
  NavAgent.targetZ[entity] = parts[2] ?? 0;
}

export const NavmeshPlugin: Plugin = {
  systems: [NavMeshLoadSystem, NavMeshBuildSystem, NavAgentPathSystem, NavAgentMoveSystem],
  recipes: [navMeshRecipe, navAgentRecipe],
  components: {
    navMesh: NavMesh,
    navAgent: NavAgent,
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
