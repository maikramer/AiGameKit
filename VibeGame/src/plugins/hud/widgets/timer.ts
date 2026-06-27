import type { State, XMLValue } from '../../../core';
import { t } from '../../i18n';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../screen-layer';
import css from '../styles/timer.css?raw';
import { formatTime, injectWidgetCss, readAttr } from './shared';

const WIDGET_TAG = 'timer';

export function createTimerWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const icon = readAttr(attributes, 'icon') ?? '';

  injectWidgetCss(css);

  const widget: HudWidget = {
    id: WIDGET_TAG,
    mount(layer: HTMLDivElement, state: State): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-timer';
      root.title = t(state, 'hud.timer');

      const iconEl = document.createElement('span');
      iconEl.className = 'hud-resource-icon';
      iconEl.textContent = icon;

      const value = document.createElement('span');
      value.className = 'hud-resource-value';
      value.textContent = '0:00';

      if (icon.length > 0) root.appendChild(iconEl);
      root.appendChild(value);
      layer.appendChild(root);

      const update = (): void => {
        value.textContent = formatTime(state.time.realtimeSinceStartup);
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const timerFactory: HudWidgetFactory = createTimerWidget;
