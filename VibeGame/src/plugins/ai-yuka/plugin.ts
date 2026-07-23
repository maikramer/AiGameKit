import type { Plugin } from '../../core';
import { YukaAgentComponent } from './components';
import { YukaAgentSystem } from './systems';

/**
 * Yuka-backed steering + decision plugin. Provides rich steering (pursuit,
 * evade, arrive, separation, flocking) layered on top of the `navmesh` crowd
 * (yuka picks the goal point, recast resolves the path). Opt-in — register via
 * `withPlugin(YukaAiPlugin)` in the game's `main.ts`. Not in `DefaultPlugins`
 * so it only runs for games that want it.
 *
 * The plugin is intentionally thin: one system ({@link YukaAgentSystem}) and
 * one component ({@link YukaAgentComponent}). The decision layer
 * (`decision.ts`) and perception (`perception.ts`) are pure helpers game code
 * calls from its entity scripts; they do not need registration.
 */
export const YukaAiPlugin: Plugin = {
  systems: [YukaAgentSystem],
  components: {
    yukaAgent: YukaAgentComponent,
  },
  config: {
    defaults: {
      'yuka-agent': {
        active: 1,
        behavior: 0,
        maxSpeed: 3,
        maxForce: 8,
      },
    },
  },
};
