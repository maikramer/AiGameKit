import type { Plugin } from '../../core';
import {
  registerRpgHudWidgetFactories,
  rpgWidgetParsers,
  rpgWidgetRecipes,
} from './widgets/rpg-widgets';

/** Registers RPG HUD widgets (health/xp/resources/boss). Requires HudPlugin. */
export const HudRpgPlugin: Plugin = {
  recipes: [...rpgWidgetRecipes],
  initialize(): void {
    registerRpgHudWidgetFactories();
  },
  config: {
    parsers: rpgWidgetParsers,
  },
};
