import type { Plugin } from '../../core';
import { WorldBorder } from './components';
import { worldBorderRecipe } from './recipes';
import { worldBorderParser } from './parser';
import { WorldBorderSystem } from './systems';

/**
 * `<WorldBorder radius="600" warn-seconds="5" margin="24">` — soft circular
 * border around the world origin. Crossing it counts down (`warn-seconds`),
 * then teleports the player back to the nearest point inside, seated on the
 * surface with velocity zeroed. Replaces falling off the map edge.
 */
export const WorldBorderPlugin: Plugin = {
  recipes: [worldBorderRecipe],
  systems: [WorldBorderSystem],
  components: { 'world-border': WorldBorder },
  config: {
    parsers: {
      WorldBorder: worldBorderParser,
    },
    defaults: {
      'world-border': {
        radius: 600,
        warnSeconds: 5,
        margin: 24,
        warnUntil: 0,
        lastShownSecond: 0,
        teleported: 0,
      },
    },
  },
};
