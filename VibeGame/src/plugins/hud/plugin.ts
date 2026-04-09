import type { Plugin, State } from '../../core';
import { HudPanel } from './components';
import { internString } from './context';
import { hudPanelRecipe } from './recipes';
import { HudBuildSystem, HudSyncSystem } from './systems';

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
  systems: [HudBuildSystem, HudSyncSystem],
  recipes: [hudPanelRecipe],
  components: {
    hudPanel: HudPanel,
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
  },
};
