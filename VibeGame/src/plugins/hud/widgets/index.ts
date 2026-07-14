import type { XMLValue } from '../../../core';

export {
  createControlsBarWidget,
  createMissionWidget,
  createTimerWidget,
  controlsBarFactory,
  missionFactory,
  timerFactory,
  CONTROLS_BAR_TAG,
  CONTROLS_BAR_TYPE,
  MISSION_TAG,
  MISSION_TYPE,
  TIMER_TAG,
  TIMER_TYPE,
  coreWidgetParsers,
  coreWidgetRecipes,
  registerCoreHudWidgetFactories,
} from './core-widgets';

export {
  createBossBarWidget,
  createHealthBarWidget,
  createResourceChipWidget,
  createTargetBarWidget,
  createXpBarWidget,
  bossBarFactory,
  healthBarFactory,
  resourceChipFactory,
  targetBarFactory,
  xpBarFactory,
  BOSS_BAR_TAG,
  BOSS_BAR_TYPE,
  HEALTH_BAR_TAG,
  HEALTH_BAR_TYPE,
  RESOURCE_CHIP_TAG,
  RESOURCE_CHIP_TYPE,
  TARGET_BAR_TAG,
  TARGET_BAR_TYPE,
  XP_BAR_TAG,
  XP_BAR_TYPE,
  registerRpgHudWidgetFactories,
  rpgWidgetParsers,
  rpgWidgetRecipes,
} from './rpg-widgets';

import {
  coreWidgetParsers,
  coreWidgetRecipes,
  registerCoreHudWidgetFactories,
} from './core-widgets';
import {
  registerRpgHudWidgetFactories,
  rpgWidgetParsers,
  rpgWidgetRecipes,
} from './rpg-widgets';

export const widgetRecipes = [...coreWidgetRecipes, ...rpgWidgetRecipes];
export const widgetParsers = { ...coreWidgetParsers, ...rpgWidgetParsers };

export function registerHudWidgetFactories(): void {
  registerCoreHudWidgetFactories();
  registerRpgHudWidgetFactories();
}

export type { XMLValue };
