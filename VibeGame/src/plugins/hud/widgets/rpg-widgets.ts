import type { Parser, Recipe } from '../../../core';
import { registerHudWidgetFactory } from '../screen-layer';
import { bossBarFactory, createBossBarWidget } from './boss-bar';
import { createHealthBarWidget, healthBarFactory } from './health-bar';
import { createResourceChipWidget, resourceChipFactory } from './resource-chip';
import { makeWidgetParser } from './shared';
import { createXpBarWidget, xpBarFactory } from './xp-bar';

export {
  createBossBarWidget,
  createHealthBarWidget,
  createResourceChipWidget,
  createXpBarWidget,
};
export { bossBarFactory, healthBarFactory, resourceChipFactory, xpBarFactory };

export const HEALTH_BAR_TAG = 'HealthBar';
export const XP_BAR_TAG = 'XpBar';
export const RESOURCE_CHIP_TAG = 'ResourceChip';
export const BOSS_BAR_TAG = 'BossBar';

export const HEALTH_BAR_TYPE = 'health-bar';
export const XP_BAR_TYPE = 'xp-bar';
export const RESOURCE_CHIP_TYPE = 'resource-chip';
export const BOSS_BAR_TYPE = 'boss-bar';

export const rpgWidgetRecipes: readonly Recipe[] = [
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
    name: BOSS_BAR_TAG,
    components: [],
    parserAttributes: ['target-entity', 'observer-entity', 'range', 'position'],
    parserOwnsChildren: true,
  },
];

export const rpgWidgetParsers: Record<string, Parser> = {
  [HEALTH_BAR_TAG]: makeWidgetParser(healthBarFactory),
  [XP_BAR_TAG]: makeWidgetParser(xpBarFactory),
  [RESOURCE_CHIP_TAG]: makeWidgetParser(resourceChipFactory),
  [BOSS_BAR_TAG]: makeWidgetParser(bossBarFactory),
};

let rpgFactoriesRegistered = false;

export function registerRpgHudWidgetFactories(): void {
  if (rpgFactoriesRegistered) return;
  rpgFactoriesRegistered = true;
  registerHudWidgetFactory(HEALTH_BAR_TYPE, healthBarFactory);
  registerHudWidgetFactory(XP_BAR_TYPE, xpBarFactory);
  registerHudWidgetFactory(RESOURCE_CHIP_TYPE, resourceChipFactory);
  registerHudWidgetFactory(BOSS_BAR_TYPE, bossBarFactory);
}
