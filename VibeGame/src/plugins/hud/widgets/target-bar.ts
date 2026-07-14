import type { State, XMLValue } from '../../../core';
import {
  getCombatTarget,
  getCombatTargetLabel,
} from '../../combat/combat-target';
import { Health } from '../../combat';
import { t } from '../../i18n';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../screen-layer';
import css from '../styles/target-bar.css?raw';
import {
  applyPosition,
  injectWidgetCss,
  readAttr,
  readPosition,
} from './shared';

const WIDGET_TAG = 'target-bar';

/**
 * HUD bar for the soft-locked combat target — sits under the compass
 * (CSS top:48px). Driven by `setCombatTarget` / `getCombatTarget`, not by a
 * fixed entity name (unlike BossBar).
 */
export function createTargetBarWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const position = readPosition(attributes);
  const id = `${WIDGET_TAG}:${readAttr(attributes, 'id') ?? 'default'}`;

  injectWidgetCss(css);

  const widget: HudWidget = {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-target';
      root.style.display = 'none';
      applyPosition(root, position, 'top-center');
      // Override preset top so we sit under the compass strip.
      if (!position || position === 'top-center') {
        root.style.top = '48px';
      }

      const nameEl = document.createElement('span');
      nameEl.className = 'hud-target-name';

      const track = document.createElement('div');
      track.className = 'hud-target-track';

      const fill = document.createElement('div');
      fill.className = 'hud-target-fill';

      const text = document.createElement('span');
      text.className = 'hud-target-text';

      track.appendChild(fill);
      track.appendChild(text);
      root.appendChild(nameEl);
      root.appendChild(track);
      layer.appendChild(root);

      const update = (): void => {
        const eid = getCombatTarget();
        const bossEid = state.getEntityByName('boss');
        // BossBar owns the boss — avoid a duplicate strip under the compass.
        if (bossEid !== null && eid === bossEid) {
          root.style.display = 'none';
          return;
        }
        if (
          eid < 0 ||
          !state.exists(eid) ||
          !state.hasComponent(eid, Health) ||
          Health.current[eid] <= 0
        ) {
          root.style.display = 'none';
          return;
        }

        root.style.display = 'block';
        const max = Health.max[eid];
        const cur = Health.current[eid];
        const ratio = max > 0 ? Math.max(0, Math.min(1, cur / max)) : 0;
        fill.style.width = `${(ratio * 100).toFixed(1)}%`;
        fill.classList.toggle('mid', ratio <= 0.5 && ratio > 0.25);
        fill.classList.toggle('low', ratio <= 0.25);

        const named =
          getCombatTargetLabel() ||
          state.getEntityName(eid) ||
          t(state, 'hud.target');
        nameEl.textContent = named;
        text.textContent = `${Math.round(cur)} / ${Math.round(max)}`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const targetBarFactory: HudWidgetFactory = createTargetBarWidget;
