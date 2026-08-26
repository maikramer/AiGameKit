import type { Parser, Recipe, State, XMLValue } from '../../../core';
import { registerHudWidgetFactory } from '../screen-layer';
import type { HudWidget, HudWidgetFactory } from '../screen-layer';
import {
  applyPosition,
  injectWidgetCss,
  makeWidgetParser,
  readAttr,
  readPosition,
  setWidgetIcon,
} from './shared';

/**
 * `<StatBar stat="stamina" icon="⚡" color="#ffd166">` — a labelled fill bar
 * driven by a game-registered source function. The game owns the value; the
 * widget only renders `cur/max` each frame (e.g. stamina, energy, water level).
 */

export const STATBAR_TAG = 'StatBar';
export const STATBAR_WIDGET_TYPE = 'stat-bar';
const LOW_RATIO = 0.25;

export interface HudStatValue {
  cur: number;
  max: number;
}

export type HudStatSource = () => HudStatValue | null;

const statSources = new WeakMap<State, Map<string, HudStatSource>>();

function ensureSourceMap(state: State): Map<string, HudStatSource> {
  let map = statSources.get(state);
  if (!map) {
    map = new Map();
    statSources.set(state, map);
  }
  return map;
}

/**
 * Register (or replace) the value source for a stat id. Returns an
 * unregister function.
 */
export function registerHudStatSource(
  state: State,
  id: string,
  source: HudStatSource
): () => void {
  ensureSourceMap(state).set(id, source);
  return () => {
    ensureSourceMap(state).delete(id);
  };
}

export function getHudStatSource(
  state: State,
  id: string
): HudStatSource | undefined {
  return statSources.get(state)?.get(id);
}

const CSS = `
.hud-statbar {
  display:flex; align-items:center; gap:8px; padding:6px 12px;
  border-radius:10px; background:rgba(10,14,26,0.62);
  font:600 12px/1 system-ui,sans-serif; color:#e8eef8;
  pointer-events:none; user-select:none;
}
.hud-statbar .hud-statbar-icon { font-size:15px; }
.hud-statbar .hud-statbar-track {
  position:relative; width:150px; height:10px; border-radius:5px;
  background:rgba(255,255,255,0.12); overflow:hidden; min-width:0;
}
.hud-statbar .hud-statbar-fill {
  position:absolute; inset:0 auto 0 0; width:0%;
  border-radius:5px; background:#6ef07a;
  transition:width 0.15s ease;
}
.hud-statbar .hud-statbar-text {
  font-variant-numeric:tabular-nums; min-width:52px; text-align:right;
}
.hud-statbar .hud-statbar-label { opacity:0.85; font-weight:500; }
.hud-statbar--low .hud-statbar-fill { animation:hud-statbar-pulse 0.9s ease-in-out infinite; }
@keyframes hud-statbar-pulse {
  0%,100% { opacity:1; } 50% { opacity:0.55; }
}
`;

export function createStatBarWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const stat = readAttr(attributes, 'stat') ?? 'default';
  const icon = readAttr(attributes, 'icon') ?? '📊';
  const color = readAttr(attributes, 'color') ?? '#6ef07a';
  const label = readAttr(attributes, 'label') ?? '';
  const position = readPosition(attributes);

  injectWidgetCss(CSS);

  const widget: HudWidget = {
    id: `${STATBAR_WIDGET_TYPE}:${stat}`,
    mount(layer: HTMLDivElement, state: State) {
      const root = document.createElement('div');
      root.className = 'hud-statbar';
      root.title = label.length > 0 ? label : stat;

      const iconEl = setWidgetIcon(icon, 'hud-statbar-icon', 'font-size:15px;');

      const track = document.createElement('div');
      track.className = 'hud-statbar-track';

      const fill = document.createElement('div');
      fill.className = 'hud-statbar-fill';
      fill.style.background = color;

      const text = document.createElement('span');
      text.className = 'hud-statbar-text';
      text.textContent = '–/–';

      track.appendChild(fill);
      root.appendChild(iconEl);
      if (label.length > 0) {
        const labelEl = document.createElement('span');
        labelEl.className = 'hud-statbar-label';
        labelEl.textContent = label;
        root.appendChild(labelEl);
      }
      root.append(track, text);
      applyPosition(root, position, 'top-left');
      layer.appendChild(root);

      const update = (): void => {
        const source = getHudStatSource(state, stat);
        const value = source ? source() : null;
        if (!value || value.max <= 0) {
          fill.style.width = '0%';
          text.textContent = '–/–';
          root.classList.remove('hud-statbar--low');
          return;
        }
        const ratio = Math.max(0, Math.min(1, value.cur / value.max));
        fill.style.width = `${(ratio * 100).toFixed(1)}%`;
        text.textContent = `${Math.round(value.cur)}/${Math.round(value.max)}`;
        root.classList.toggle('hud-statbar--low', ratio <= LOW_RATIO);
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const statBarFactory: HudWidgetFactory = createStatBarWidget;

export const statBarRecipe: Recipe = {
  name: STATBAR_TAG,
  components: [],
  parserAttributes: ['stat', 'icon', 'color', 'label', 'position'],
  parserOwnsChildren: true,
};

export const statBarParser: Parser = makeWidgetParser(statBarFactory);

let factoryRegistered = false;

/** Idempotent factory registration (hud plugin initialize). */
export function registerStatBarWidgetFactory(): void {
  if (factoryRegistered) return;
  factoryRegistered = true;
  registerHudWidgetFactory(STATBAR_WIDGET_TYPE, statBarFactory);
}
