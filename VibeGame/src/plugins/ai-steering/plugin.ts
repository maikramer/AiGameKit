import type { Plugin } from '../../core';
import { SteeringAgent, SteeringTarget } from './components';
import { npcRecipe } from './recipes';
import { SteeringSyncSystem } from './systems';

export const AiSteeringPlugin: Plugin = {
  systems: [SteeringSyncSystem],
  recipes: [npcRecipe],
  components: {
    steeringAgent: SteeringAgent,
    steeringTarget: SteeringTarget,
  },
  config: {
    defaults: {
      steeringAgent: {
        behavior: 0,
        maxSpeed: 3,
        maxForce: 10,
        active: 1,
      },
      steeringTarget: {
        targetEntity: 0,
        targetX: 0,
        targetY: 0,
        targetZ: 0,
      },
    },
    enums: {
      steeringAgent: {
        behavior: {
          seek: 0,
          wander: 1,
          flee: 2,
        },
      },
    },
  },
};
