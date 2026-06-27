import type { State, XMLValue } from '../../../core';
import { t } from '../../i18n';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../screen-layer';
import css from '../styles/mission.css?raw';
import { injectWidgetCss, readAttr } from './shared';

const WIDGET_TAG = 'mission';

export function createMissionWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const titleKey = readAttr(attributes, 'title-key') ?? 'hud.mission.title';
  const bodyKey = readAttr(attributes, 'body-key') ?? 'hud.mission';

  injectWidgetCss(css);

  const widget: HudWidget = {
    id: WIDGET_TAG,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-mission';

      const title = document.createElement('span');
      title.className = 'hud-mission-title';
      title.textContent = t(state, titleKey);

      const body = document.createElement('span');
      body.className = 'hud-mission-body';
      body.textContent = t(state, bodyKey);

      root.appendChild(title);
      root.appendChild(body);
      layer.appendChild(root);

      const update = (): void => {
        title.textContent = t(state, titleKey);
        body.textContent = t(state, bodyKey);
      };

      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const missionFactory: HudWidgetFactory = createMissionWidget;
