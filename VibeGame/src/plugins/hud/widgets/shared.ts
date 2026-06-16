import type { Parser, State, XMLValue } from '../../../core';
import { registerHudWidget } from '../screen-layer';
import type { HudWidget, HudWidgetFactory } from '../screen-layer';

const STYLE_ELEMENT_ID = 'vibegame-hud-widgets';
const registeredCss = new Set<string>();

export function injectWidgetCss(css: string): void {
  if (typeof document === 'undefined') return;
  if (registeredCss.has(css)) return;
  registeredCss.add(css);
  let style = document.getElementById(STYLE_ELEMENT_ID);
  if (!style) {
    style = document.createElement('style');
    style.id = STYLE_ELEMENT_ID;
    document.head.appendChild(style);
  }
  style.textContent += css;
}

export function readAttr(
  attributes: Record<string, XMLValue>,
  key: string
): string | undefined {
  const v = attributes[key];
  if (v === undefined || v === null) return undefined;
  const s = String(v).trim();
  return s.length === 0 ? undefined : s;
}

export function resolveTargetEntity(
  state: State,
  raw: string | undefined,
  fallbackNames: readonly string[] = ['hero', 'player']
): number | null {
  if (raw) {
    if (/^-?\d+$/.test(raw)) {
      const n = Number(raw);
      if (Number.isInteger(n) && n >= 0) return n;
    }
    const named = state.getEntityByName(raw);
    if (named !== null) return named;
  }
  for (const name of fallbackNames) {
    const found = state.getEntityByName(name);
    if (found !== null) return found;
  }
  return null;
}

export function makeWidgetParser(factory: HudWidgetFactory): Parser {
  return ({ element, state }) => {
    const widget = factory(element.attributes, state);
    registerHudWidget(state, widget);
  };
}

export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${r.toString().padStart(2, '0')}`;
}

export type HudPosition =
  | 'top-left'
  | 'top-right'
  | 'top-center'
  | 'bottom-left'
  | 'bottom-right'
  | 'bottom-center';

const POSITION_PRESETS: Record<HudPosition, string> = {
  'top-left': 'position:absolute;top:18px;left:18px;',
  'top-right': 'position:absolute;top:18px;right:18px;',
  'top-center':
    'position:absolute;top:14px;left:50%;transform:translateX(-50%);',
  'bottom-left': 'position:absolute;bottom:22px;left:18px;',
  'bottom-right': 'position:absolute;bottom:22px;right:18px;',
  'bottom-center':
    'position:absolute;bottom:22px;left:50%;transform:translateX(-50%);',
};

export function applyPosition(
  root: HTMLElement,
  position: HudPosition | undefined,
  fallback: HudPosition
): void {
  const pos = position ?? fallback;
  root.style.cssText += POSITION_PRESETS[pos];
}

export function readPosition(
  attributes: Record<string, XMLValue>
): HudPosition | undefined {
  const raw = readAttr(attributes, 'position');
  if (!raw) return undefined;
  return (POSITION_PRESETS as Record<string, string>)[raw]
    ? (raw as HudPosition)
    : undefined;
}

export type { HudWidget, HudWidgetFactory };
