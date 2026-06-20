import { logger } from '../../core/utils/logger';
import type { ParserParams, State, System, XMLValue } from '../../core';

const HUD_SCREEN_LAYER_CLASS = 'vibe-hud-screen-layer';
const HUD_SCREEN_LAYER_STYLE =
  'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;';

export interface WidgetHandle {
  root: HTMLElement;
  update?(state: State): void;
  unmount(): void;
}

export interface HudWidget {
  id: string;
  mount(layer: HTMLDivElement, state: State): WidgetHandle;
}

export type HudWidgetFactory = (
  attributes: Record<string, XMLValue>,
  state: State
) => HudWidget;

interface MountedWidget {
  widget: HudWidget;
  handle: WidgetHandle;
}

const stateToLayer = new WeakMap<State, HTMLDivElement>();
const stateToWidgets = new WeakMap<State, MountedWidget[]>();
const widgetFactories = new Map<string, HudWidgetFactory>();

export function registerHudWidgetFactory(
  type: string,
  factory: HudWidgetFactory
): void {
  widgetFactories.set(type, factory);
}

export function getHudWidgetFactory(
  type: string
): HudWidgetFactory | undefined {
  return widgetFactories.get(type);
}

export function createHudScreenLayer(state: State): HTMLDivElement {
  const existing = stateToLayer.get(state);
  if (existing) return existing;
  const layer = document.createElement('div');
  layer.className = HUD_SCREEN_LAYER_CLASS;
  layer.setAttribute('aria-hidden', 'true');
  layer.style.cssText = HUD_SCREEN_LAYER_STYLE;
  document.body.appendChild(layer);
  stateToLayer.set(state, layer);
  if (!stateToWidgets.has(state)) {
    stateToWidgets.set(state, []);
  }
  return layer;
}

export function getHudScreenLayer(state: State): HTMLDivElement {
  const layer = stateToLayer.get(state);
  return layer ?? createHudScreenLayer(state);
}

export function registerHudWidget(state: State, widget: HudWidget): void {
  const layer = getHudScreenLayer(state);
  const widgets = stateToWidgets.get(state) ?? [];
  if (widgets.some((m) => m.widget.id === widget.id)) return;
  const handle = widget.mount(layer, state);
  widgets.push({ widget, handle });
  stateToWidgets.set(state, widgets);
}

export function unregisterHudWidget(state: State, widgetId: string): void {
  const widgets = stateToWidgets.get(state);
  if (!widgets) return;
  const idx = widgets.findIndex((m) => m.widget.id === widgetId);
  if (idx < 0) return;
  const [removed] = widgets.splice(idx, 1);
  try {
    removed.handle.unmount();
  } catch (err) {
    logger.error('[VibeGame] HudWidget unmount error:', err);
  }
}

export const HudScreenUpdateSystem: System = {
  group: 'late',
  update(state: State): void {
    const widgets = stateToWidgets.get(state);
    if (!widgets || widgets.length === 0) return;
    for (const m of widgets) {
      if (!m.handle.update) continue;
      try {
        m.handle.update(state);
      } catch (err) {
        logger.error('[VibeGame] HudWidget update error:', err);
      }
    }
  },
  dispose(state: State): void {
    const widgets = stateToWidgets.get(state);
    if (widgets) {
      for (const m of widgets) {
        try {
          m.handle.unmount();
        } catch (err) {
          logger.error('[VibeGame] HudWidget unmount error:', err);
        }
      }
    }
    stateToWidgets.delete(state);
    const layer = stateToLayer.get(state);
    if (layer && layer.parentNode) {
      layer.parentNode.removeChild(layer);
    }
    stateToLayer.delete(state);
  },
};

export function hudScreenLayerParser(): void {
  // The layer is created eagerly by HudPlugin.initialize; the <HudScreenLayer/>
  // element is a declarative marker, so the parser has nothing to do at parse
  // time. Defining it keeps the XML parser from warning about an unknown tag.
}

export function hudWidgetParser({ element, state }: ParserParams): void {
  const raw = element.attributes.type;
  if (raw === undefined || raw === null) return;
  const type = String(raw).trim();
  if (type.length === 0) return;
  const factory = getHudWidgetFactory(type);
  if (!factory) {
    logger.warn(`[VibeGame] Unknown HudWidget type: "${type}"`);
    return;
  }
  const widget = factory(element.attributes, state);
  registerHudWidget(state, widget);
}
