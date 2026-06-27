import type { Parser, Recipe, XMLValue } from '../../../core';
import { registerHudWidgetFactory } from '../screen-layer';
import { bossBarFactory, createBossBarWidget } from './boss-bar';
import { controlsBarFactory, createControlsBarWidget } from './controls-bar';
import { createHealthBarWidget, healthBarFactory } from './health-bar';
import { createMissionWidget, missionFactory } from './mission';
import { createResourceChipWidget, resourceChipFactory } from './resource-chip';
import { makeWidgetParser } from './shared';
import { createTimerWidget, timerFactory } from './timer';
import { createXpBarWidget, xpBarFactory } from './xp-bar';

export {
  createBossBarWidget,
  createControlsBarWidget,
  createHealthBarWidget,
  createMissionWidget,
  createResourceChipWidget,
  createTimerWidget,
  createXpBarWidget,
};
export {
  bossBarFactory,
  controlsBarFactory,
  healthBarFactory,
  missionFactory,
  resourceChipFactory,
  timerFactory,
  xpBarFactory,
};

export const HEALTH_BAR_TAG = 'HealthBar';
export const XP_BAR_TAG = 'XpBar';
export const RESOURCE_CHIP_TAG = 'ResourceChip';
export const MISSION_TAG = 'Mission';
export const TIMER_TAG = 'Timer';
export const BOSS_BAR_TAG = 'BossBar';
export const CONTROLS_BAR_TAG = 'ControlsBar';

export const HEALTH_BAR_TYPE = 'health-bar';
export const XP_BAR_TYPE = 'xp-bar';
export const RESOURCE_CHIP_TYPE = 'resource-chip';
export const MISSION_TYPE = 'mission';
export const TIMER_TYPE = 'timer';
export const BOSS_BAR_TYPE = 'boss-bar';
export const CONTROLS_BAR_TYPE = 'controls-bar';

export const widgetRecipes: readonly Recipe[] = [
  {
    name: HEALTH_BAR_TAG,
    components: [],
    parserAttributes: ['target-entity', 'icon', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: XP_BAR_TAG,
    components: [],
    parserAttributes: ['target-entity', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: RESOURCE_CHIP_TAG,
    components: [],
    parserAttributes: ['resource', 'icon', 'target-entity', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: MISSION_TAG,
    components: [],
    parserAttributes: ['title-key', 'body-key', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: TIMER_TAG,
    components: [],
    parserAttributes: ['icon', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: BOSS_BAR_TAG,
    components: [],
    parserAttributes: ['target-entity', 'observer-entity', 'range', 'position'],
    parserOwnsChildren: true,
  },
  {
    name: CONTROLS_BAR_TAG,
    components: [],
    parserAttributes: ['text-key', 'position'],
    parserOwnsChildren: true,
  },
];

export const widgetParsers: Record<string, Parser> = {
  [HEALTH_BAR_TAG]: makeWidgetParser(healthBarFactory),
  [XP_BAR_TAG]: makeWidgetParser(xpBarFactory),
  [RESOURCE_CHIP_TAG]: makeWidgetParser(resourceChipFactory),
  [MISSION_TAG]: makeWidgetParser(missionFactory),
  [TIMER_TAG]: makeWidgetParser(timerFactory),
  [BOSS_BAR_TAG]: makeWidgetParser(bossBarFactory),
  [CONTROLS_BAR_TAG]: makeWidgetParser(controlsBarFactory),
};

let factoriesRegistered = false;

export function registerHudWidgetFactories(): void {
  if (factoriesRegistered) return;
  factoriesRegistered = true;
  registerHudWidgetFactory(HEALTH_BAR_TYPE, healthBarFactory);
  registerHudWidgetFactory(XP_BAR_TYPE, xpBarFactory);
  registerHudWidgetFactory(RESOURCE_CHIP_TYPE, resourceChipFactory);
  registerHudWidgetFactory(MISSION_TYPE, missionFactory);
  registerHudWidgetFactory(TIMER_TYPE, timerFactory);
  registerHudWidgetFactory(BOSS_BAR_TYPE, bossBarFactory);
  registerHudWidgetFactory(CONTROLS_BAR_TYPE, controlsBarFactory);
}

export type { XMLValue };
