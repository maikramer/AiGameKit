import type { State, XMLValue } from '../../../core';
import { Health } from '../../combat';
import { t } from '../../i18n';
import { Transform } from '../../transforms';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../screen-layer';
import css from '../styles/boss-bar.css?raw';
import {
  applyPosition,
  injectWidgetCss,
  readAttr,
  readPosition,
  resolveTargetEntity,
} from './shared';

const WIDGET_TAG = 'boss-bar';
const DEFAULT_TARGET_NAMES = ['boss'];
const DEFAULT_OBSERVER_NAMES = ['hero', 'player'];
const DEFAULT_RANGE = 60;

export function createBossBarWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const targetRaw = readAttr(attributes, 'target-entity');
  const observerRaw = readAttr(attributes, 'observer-entity');
  const range = Number(readAttr(attributes, 'range') ?? DEFAULT_RANGE);
  const rangeNum = Number.isFinite(range) && range > 0 ? range : DEFAULT_RANGE;
  const position = readPosition(attributes);
  const resolvedAtMount =
    resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
  const id = `${WIDGET_TAG}:${resolvedAtMount >= 0 ? resolvedAtMount : (targetRaw ?? 'boss')}`;

  injectWidgetCss(css);

  const widget: HudWidget = {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-boss';
      root.style.display = 'none';
      applyPosition(root, position, 'top-center');

      const track = document.createElement('div');
      track.className = 'hud-boss-track';

      const fill = document.createElement('div');
      fill.className = 'hud-boss-fill';

      const text = document.createElement('span');
      text.className = 'hud-boss-text';

      track.appendChild(fill);
      track.appendChild(text);
      root.appendChild(track);
      layer.appendChild(root);

      const update = (): void => {
        const bossEid =
          resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
        const observerEid =
          resolveTargetEntity(state, observerRaw, DEFAULT_OBSERVER_NAMES) ?? -1;

        if (
          bossEid < 0 ||
          observerEid < 0 ||
          !state.hasComponent(bossEid, Health) ||
          !state.hasComponent(bossEid, Transform) ||
          !state.hasComponent(observerEid, Transform) ||
          Health.current[bossEid] <= 0
        ) {
          root.style.display = 'none';
          return;
        }

        const dx = Transform.posX[bossEid] - Transform.posX[observerEid];
        const dz = Transform.posZ[bossEid] - Transform.posZ[observerEid];
        const distSq = dx * dx + dz * dz;
        if (distSq > rangeNum * rangeNum) {
          root.style.display = 'none';
          return;
        }

        root.style.display = 'block';
        const max = Health.max[bossEid];
        const cur = Health.current[bossEid];
        const ratio = max > 0 ? Math.max(0, Math.min(1, cur / max)) : 0;
        fill.style.width = `${(ratio * 100).toFixed(1)}%`;
        const label = t(state, 'hud.boss');
        text.textContent = `${label}: ${Math.round(cur)}/${Math.round(max)}`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const bossBarFactory: HudWidgetFactory = createBossBarWidget;
