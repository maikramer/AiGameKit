import type { Plugin } from '../../core';
import {
  YukaAgentComponent,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_SEEK,
  YUKA_BEHAVIOR_WANDER,
} from './components';
import { npcRecipe } from './recipes';
import { YukaAgentSystem } from './systems';

/**
 * Yuka-backed steering + decision plugin. Provides rich steering (pursuit,
 * evade, arrive, separation, flocking) layered on top of the `navmesh` crowd
 * when a {@link NavMeshAgent} is present (yuka picks the goal point, recast
 * resolves the path). Without a crowd agent, the system writes planar
 * `Transform` directly so declarative `<NPC>` entities still move.
 *
 * The decision layer (`decision.ts`) and perception (`perception.ts`) are pure
 * helpers game code calls from its entity scripts; they do not need registration.
 */
export const YukaAiPlugin: Plugin = {
  systems: [YukaAgentSystem],
  recipes: [npcRecipe],
  components: {
    yukaAgent: YukaAgentComponent,
  },
  config: {
    defaults: {
      'yuka-agent': {
        active: 1,
        behavior: YUKA_BEHAVIOR_SEEK,
        maxSpeed: 3,
        maxForce: 8,
        targetEid: 0,
        targetX: 0,
        targetZ: 0,
      },
    },
    enums: {
      'yuka-agent': {
        behavior: {
          seek: YUKA_BEHAVIOR_SEEK,
          wander: YUKA_BEHAVIOR_WANDER,
          flee: YUKA_BEHAVIOR_FLEE,
        },
      },
    },
  },
};
