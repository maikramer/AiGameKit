import type { State, XMLValue } from '../../../core';
import { Health } from '../../combat';
import { t } from '../../i18n';
import type { HudWidget, HudWidgetFactory, WidgetHandle } from '../screen-layer';
import css from '../styles/health-bar.css?raw';
import {
  injectWidgetCss,
  readAttr,
  resolveTargetEntity,
} from './shared';

const WIDGET_TAG = 'health-bar';
const DEFAULT_TARGET_NAMES = ['hero', 'player'];

export interface HealthBarWidgetOptions {
  targetEntity?: string;
  icon?: string;
}

export function createHealthBarWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target-entity');
  const icon = readAttr(attributes, 'icon') ?? '❤';
  const resolvedAtMount =
    resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
  const id = `${WIDGET_TAG}:${resolvedAtMount >= 0 ? resolvedAtMount : targetRaw ?? 'default'}`;

  injectWidgetCss(css);

  const widget: HudWidget = {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-health';
      root.title = t(state, 'hud.health');

      const iconEl = document.createElement('span');
      iconEl.className = 'hud-health-icon';
      iconEl.textContent = icon;

      const track = document.createElement('div');
      track.className = 'hud-health-track';

      const fill = document.createElement('div');
      fill.className = 'hud-health-fill';

      const text = document.createElement('span');
      text.className = 'hud-health-text';

      track.appendChild(fill);
      track.appendChild(text);
      root.appendChild(iconEl);
      root.appendChild(track);
      layer.appendChild(root);

      const update = (): void => {
        const eid =
          resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
        if (eid < 0 || !state.hasComponent(eid, Health)) {
          fill.style.width = '0%';
          text.textContent = `0/0`;
          return;
        }
        const max = Health.max[eid];
        const cur = Health.current[eid];
        const ratio = max > 0 ? Math.max(0, Math.min(1, cur / max)) : 0;
        fill.style.width = `${(ratio * 100).toFixed(1)}%`;
        text.textContent = `${Math.round(cur)}/${Math.round(max)}`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const healthBarFactory: HudWidgetFactory = createHealthBarWidget;
