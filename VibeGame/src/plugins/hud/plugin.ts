import { parseColorValue } from '../../core';
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
  coreWidgetParsers,
  coreWidgetRecipes,
  registerCoreHudWidgetFactories,
} from './widgets/core-widgets';
import {
  waypointArrowParser,
  waypointArrowRecipe,
} from './widgets/waypoint-arrow';
import {
  hotbarParser,
  hotbarRecipe,
  registerHotbarWidgetFactory,
} from './widgets/hotbar';
import {
  registerStatBarWidgetFactory,
  statBarParser,
  statBarRecipe,
} from './widgets/stat-bar';

const minimapRecipe: Recipe = {
  name: 'Minimap',
  components: [],
  parserOwnsChildren: true,
  parserAttributes: [
    'range',
    'size',
    'anchor',
    'categories',
    'color-player',
    'color-enemy',
    'color-boss',
    'color-merchant',
    'color-wood',
    'color-stone',
    'color-neutral',
  ],
};

function textAdapter(entity: number, value: string, state: State): void {
  HudPanel.textIndex[entity] = internString(state, value);
}

function colorAdapter(entity: number, value: string, _state: State): void {
  // XMLValueParser pre-converts "#hex"/"0xhex" to numbers, stringified back
  // here — parseColorValue handles that round-trip and bare hex strings.
  const n = parseColorValue(value);
  if (Number.isNaN(n)) return;
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
    waypointArrowRecipe,
    hotbarRecipe,
    statBarRecipe,
    ...coreWidgetRecipes,
  ],
  components: {
    hudPanel: HudPanel,
  },
  initialize(state: State): void {
    registerCoreHudWidgetFactories();
    if (state.headless) return;
    if (typeof document === 'undefined') return;
    createHudScreenLayer(state);
    registerMinimapWidgetFactory();
    registerHotbarWidgetFactory();
    registerStatBarWidgetFactory();
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
      WaypointArrow: waypointArrowParser,
      Hotbar: hotbarParser,
      StatBar: statBarParser,
      TabbedModal: tabbedModalParser,
      ...coreWidgetParsers,
    },
  },
};

export {
  MinimapWidget,
  minimapParser,
  minimapRecipe,
  registerMinimapWidgetFactory,
};
