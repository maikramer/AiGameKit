import type { Parser, State, XMLValue } from '../../core';
import {
  registerHudWidgetFactory,
  registerHudWidget,
} from '../hud/screen-layer';
import type { HudWidget, HudWidgetFactory } from '../hud/screen-layer';
import { injectWidgetCss, readAttr } from '../hud/widgets/shared';
import { GameClock } from './components';
import { formatMinute, getClockEntity } from './api';
import { SEASON_NAMES } from './calendar';

/**
 * `<Clock position="top-right">` — day/season + wall clock readout fed by the
 * first `GameClock`. Registered by the daycycle plugin's initialize, so a game
 * gets the widget by adding the plugin (no hud-plugin edits).
 */

export const CLOCK_TAG = 'Clock';

const CSS = `
.hud-clock {
  position: absolute;
  top: 14px;
  right: 16px;
  display: flex;
  gap: 10px;
  align-items: baseline;
  padding: 6px 12px;
  border-radius: 10px;
  background: rgba(10, 22, 14, 0.62);
  color: #e8f4dc;
  font: 600 13px/1 system-ui, sans-serif;
  letter-spacing: 0.3px;
  pointer-events: none;
  user-select: none;
}
.hud-clock .hud-clock-time { font-size: 15px; font-variant-numeric: tabular-nums; }
.hud-clock .hud-clock-date { opacity: 0.85; font-weight: 500; }
`;

export function createClockWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const timeIcon = readAttr(attributes, 'icon') ?? '🕘';

  injectWidgetCss(CSS);

  return {
    id: 'clock',
    mount(layer: HTMLDivElement, state: State) {
      const root = document.createElement('div');
      root.className = 'hud-clock';

      const icon = document.createElement('span');
      icon.textContent = timeIcon;
      const time = document.createElement('span');
      time.className = 'hud-clock-time';
      time.textContent = '--:--';
      const date = document.createElement('span');
      date.className = 'hud-clock-date';

      root.append(icon, time, date);
      layer.appendChild(root);

      const update = (): void => {
        const eid = getClockEntity(state);
        if (!eid) {
          time.textContent = '--:--';
          date.textContent = '';
          return;
        }
        time.textContent = formatMinute(GameClock.minuteOfDay[eid]);
        const season = SEASON_NAMES[GameClock.season[eid]] ?? '';
        date.textContent = `${season} ${GameClock.day[eid]} · yr ${GameClock.year[eid]}`;
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };
}

export const clockFactory: HudWidgetFactory = createClockWidget;

export const clockWidgetParser: Parser = ({ element, state }) => {
  registerHudWidget(state, createClockWidget(element.attributes, state));
};

let factoryRegistered = false;

/** Idempotent factory registration (plugin initialize). */
export function registerClockHudWidget(): void {
  if (factoryRegistered) return;
  factoryRegistered = true;
  registerHudWidgetFactory('clock', clockFactory);
}
