import type { Plugin, Recipe, State } from '../../core';
import { HudPanel } from './components';
import { internString } from './context';
import {
  HudScreenUpdateSystem,
  createHudScreenLayer,
  hudScreenLayerParser,
  hudWidgetParser,
} from './screen-layer';
import {
  compassRecipe,
  hudPanelRecipe,
  hudScreenLayerRecipe,
  hudWidgetRecipe,
} from './recipes';
import { HudBuildSystem, HudSyncSystem } from './systems';
import { compassParser } from './widgets/compass';
import {
  interactionPromptParser,
  interactionPromptRecipe,
} from './widgets/interaction-prompt';
import { tabbedModalParser, tabbedModalRecipe } from './widgets/tabbed-modal';
import {
  MinimapWidget,
  minimapParser,
  registerMinimapWidgetFactory,
} from './widgets/minimap';
import {
  registerHudWidgetFactories,
  widgetParsers,
  widgetRecipes,
} from './widgets';

const minimapRecipe: Recipe = {
  name: 'Minimap',
  components: [],
  parserOwnsChildren: true,
};

function textAdapter(entity: number, value: string, state: State): void {
  HudPanel.textIndex[entity] = internString(state, value);
}

function colorAdapter(entity: number, value: string, _state: State): void {
  const n = value.startsWith('#')
    ? parseInt(value.slice(1), 16)
    : Number(value);
  const r = ((n >> 16) & 0xff) / 255;
  const g = ((n >> 8) & 0xff) / 255;
  const b = (n & 0xff) / 255;
  HudPanel.bgR[entity] = r;
  HudPanel.bgG[entity] = g;
  HudPanel.bgB[entity] = b;
}

export const HudPlugin: Plugin = {
  systems: [HudBuildSystem, HudSyncSystem, HudScreenUpdateSystem],
  recipes: [
    hudPanelRecipe,
    hudScreenLayerRecipe,
    hudWidgetRecipe,
    compassRecipe,
    interactionPromptRecipe,
    tabbedModalRecipe,
    minimapRecipe,
    ...widgetRecipes,
  ],
  components: {
    hudPanel: HudPanel,
  },
  initialize(state: State): void {
    registerHudWidgetFactories();
    if (state.headless) return;
    if (typeof document === 'undefined') return;
    createHudScreenLayer(state);
    registerMinimapWidgetFactory();
  },
  config: {
    defaults: {
      hudPanel: {
        width: 1.2,
        height: 0.35,
        bgR: 0,
        bgG: 0,
        bgB: 0,
        opacity: 0.75,
        textIndex: 0,
        built: 0,
      },
    },
    adapters: {
      'hud-panel': {
        text: textAdapter,
        'bg-color': colorAdapter,
      },
    },
    parsers: {
      HudScreenLayer: hudScreenLayerParser,
      HudWidget: hudWidgetParser,
      Compass: compassParser,
      Minimap: minimapParser,
      InteractionPrompt: interactionPromptParser,
      TabbedModal: tabbedModalParser,
      ...widgetParsers,
    },
  },
};

export {
  MinimapWidget,
  minimapParser,
  minimapRecipe,
  registerMinimapWidgetFactory,
};
