import type { State, XMLValue } from '../../../core';
import { ProgressionComponent, getXpToNextLevel } from '../../rpg-progression';
import type { HudWidget, HudWidgetFactory, WidgetHandle } from '../screen-layer';
import css from '../styles/xp-bar.css?raw';
import {
  injectWidgetCss,
  readAttr,
  resolveTargetEntity,
} from './shared';

const WIDGET_TAG = 'xp-bar';
const DEFAULT_TARGET_NAMES = ['hero', 'player'];

export function createXpBarWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target-entity');
  const resolvedAtMount =
    resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
  const id = `${WIDGET_TAG}:${resolvedAtMount >= 0 ? resolvedAtMount : targetRaw ?? 'default'}`;

  injectWidgetCss(css);

  const widget: HudWidget = {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-xp';

      const level = document.createElement('div');
      level.className = 'hud-xp-level';
      level.textContent = '1';

      const track = document.createElement('div');
      track.className = 'hud-xp-track';

      const fill = document.createElement('div');
      fill.className = 'hud-xp-fill';

      track.appendChild(fill);
      root.appendChild(level);
      root.appendChild(track);
      layer.appendChild(root);

      const update = (): void => {
        const eid =
          resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
        if (eid < 0 || !state.hasComponent(eid, ProgressionComponent)) {
          fill.style.width = '0%';
          level.textContent = '1';
          return;
        }
        const lvl = ProgressionComponent.level[eid];
        const xp = ProgressionComponent.xp[eid];
        const needed = getXpToNextLevel(state, eid);
        level.textContent = String(lvl);
        const ratio = needed > 0 ? Math.max(0, Math.min(1, xp / needed)) : 0;
        fill.style.width = `${(ratio * 100).toFixed(1)}%`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const xpBarFactory: HudWidgetFactory = createXpBarWidget;
