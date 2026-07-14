import type { Parser, Recipe } from '../../../core';
import { registerHudWidgetFactory } from '../screen-layer';
import { controlsBarFactory, createControlsBarWidget } from './controls-bar';
import { createMissionWidget, missionFactory } from './mission';
import { makeWidgetParser } from './shared';
import { createTimerWidget, timerFactory } from './timer';

export { createControlsBarWidget, createMissionWidget, createTimerWidget };
export { controlsBarFactory, missionFactory, timerFactory };

export const MISSION_TAG = 'Mission';
export const TIMER_TAG = 'Timer';
export const CONTROLS_BAR_TAG = 'ControlsBar';

export const MISSION_TYPE = 'mission';
export const TIMER_TYPE = 'timer';
export const CONTROLS_BAR_TYPE = 'controls-bar';

export const coreWidgetRecipes: readonly Recipe[] = [
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
    name: CONTROLS_BAR_TAG,
    components: [],
    parserAttributes: ['text-key', 'position'],
    parserOwnsChildren: true,
  },
];

export const coreWidgetParsers: Record<string, Parser> = {
  [MISSION_TAG]: makeWidgetParser(missionFactory),
  [TIMER_TAG]: makeWidgetParser(timerFactory),
  [CONTROLS_BAR_TAG]: makeWidgetParser(controlsBarFactory),
};

let coreFactoriesRegistered = false;

export function registerCoreHudWidgetFactories(): void {
  if (coreFactoriesRegistered) return;
  coreFactoriesRegistered = true;
  registerHudWidgetFactory(MISSION_TYPE, missionFactory);
  registerHudWidgetFactory(TIMER_TYPE, timerFactory);
  registerHudWidgetFactory(CONTROLS_BAR_TYPE, controlsBarFactory);
}
