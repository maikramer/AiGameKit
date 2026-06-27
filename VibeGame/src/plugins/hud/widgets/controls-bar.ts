import type { State, XMLValue } from '../../../core';
import { t } from '../../i18n';
import type {
  HudWidget,
  HudWidgetFactory,
  WidgetHandle,
} from '../screen-layer';
import css from '../styles/controls-bar.css?raw';
import {
  applyPosition,
  injectWidgetCss,
  readAttr,
  readPosition,
} from './shared';

const WIDGET_TAG = 'controls';

export function createControlsBarWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const textKey = readAttr(attributes, 'text-key') ?? 'hud.controls';
  const position = readPosition(attributes);

  injectWidgetCss(css);

  const widget: HudWidget = {
    id: WIDGET_TAG,
    mount(layer: HTMLDivElement, state: State): WidgetHandle {
      const root = document.createElement('div');
      root.className = 'hud-controls';
      applyPosition(root, position, 'bottom-center');
      root.textContent = t(state, textKey);
      layer.appendChild(root);

      const update = (): void => {
        root.textContent = t(state, textKey);
      };

      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const controlsBarFactory: HudWidgetFactory = createControlsBarWidget;
