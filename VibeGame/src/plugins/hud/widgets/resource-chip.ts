import type { State, XMLValue } from '../../../core';
import { getResource } from '../../rpg-vault';
import { t } from '../../i18n';
import type { HudWidget, HudWidgetFactory, WidgetHandle } from '../screen-layer';
import css from '../styles/resource-chip.css?raw';
import {
  injectWidgetCss,
  readAttr,
  resolveTargetEntity,
} from './shared';

const WIDGET_TAG = 'resource';
const DEFAULT_TARGET_NAMES = ['hero', 'player'];
const SUPPORTED_RESOURCES = ['gold', 'wood', 'stone'] as const;
type ResourceKind = (typeof SUPPORTED_RESOURCES)[number];

function i18nKeyFor(kind: ResourceKind): string {
  return `hud.${kind}`;
}

export function createResourceChipWidget(
  attributes: Record<string, XMLValue>,
  state: State
): HudWidget {
  const resourceRaw = readAttr(attributes, 'resource');
  const kind: ResourceKind =
    resourceRaw && (SUPPORTED_RESOURCES as readonly string[]).includes(resourceRaw)
      ? (resourceRaw as ResourceKind)
      : 'gold';
  const icon = readAttr(attributes, 'icon') ?? '';
  const targetRaw = readAttr(attributes, 'target-entity');
  const resolvedAtMount =
    resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
  const id = `${WIDGET_TAG}:${kind}:${resolvedAtMount >= 0 ? resolvedAtMount : targetRaw ?? 'default'}`;

  injectWidgetCss(css);

  const widget: HudWidget = {
    id,
    mount(layer: HTMLDivElement): WidgetHandle {
      const root = document.createElement('div');
      root.className = `hud-resource hud-resource-${kind}`;
      root.title = t(state, i18nKeyFor(kind));

      const iconEl = document.createElement('span');
      iconEl.className = 'hud-resource-icon';
      iconEl.textContent = icon;

      const value = document.createElement('span');
      value.className = 'hud-resource-value';
      value.textContent = '0';

      root.appendChild(iconEl);
      root.appendChild(value);
      layer.appendChild(root);

      const update = (): void => {
        const eid =
          resolveTargetEntity(state, targetRaw, DEFAULT_TARGET_NAMES) ?? -1;
        const amount = eid >= 0 ? getResource(state, eid, kind) : 0;
        value.textContent = String(Math.floor(amount));
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const resourceChipFactory: HudWidgetFactory = createResourceChipWidget;
