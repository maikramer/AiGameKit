import type { Parser, Recipe, State, XMLValue } from '../../../core';
import { isKeyDown } from '../../input';
import { registerHudWidgetFactory } from '../screen-layer';
import type { HudWidget, HudWidgetFactory } from '../screen-layer';
import {
  applyPosition,
  injectWidgetCss,
  makeWidgetParser,
  readPosition,
} from './shared';
import { createHudSlot } from './slot';

/**
 * `<Hotbar position="bottom-center">` — a fixed row of quick-use slots
 * selected with Digit1–6 (or by clicking). Game code owns the contents:
 * `setHotbarSlots(state, specs)` repaints the row, `onHotbarActivate` is how
 * tool/seed selection hears about the player's choice.
 */

export const HOTBAR_TAG = 'Hotbar';
export const HOTBAR_WIDGET_TYPE = 'hotbar';
export const HOTBAR_KEYS = [
  'Digit1',
  'Digit2',
  'Digit3',
  'Digit4',
  'Digit5',
  'Digit6',
] as const;

export interface HotbarSlotSpec {
  /** Image path or emoji, same convention as HudSlotSpec. */
  icon: string;
  label: string;
  /** Border tint (hex). */
  color: string;
  /** Owned count shown on the bottom-right badge; omit/0 hides it. */
  count?: number;
}

export type HotbarActivateListener = (index: number) => void;

interface HotbarData {
  slots: HotbarSlotSpec[];
  active: number;
  listeners: HotbarActivateListener[];
  /** Per-key held state so a held Digit key activates exactly once. */
  latch: boolean[];
  /** Bumped by setHotbarSlots; the widget rebuilds its cards when it changes. */
  version: number;
}

const hotbarByState = new WeakMap<State, HotbarData>();

function ensureHotbarData(state: State): HotbarData {
  let data = hotbarByState.get(state);
  if (!data) {
    data = {
      slots: [],
      active: 0,
      listeners: [],
      latch: new Array(HOTBAR_KEYS.length).fill(false),
      version: 0,
    };
    hotbarByState.set(state, data);
  }
  return data;
}

/**
 * Index of the slot whose key went down this frame (edge-triggered), or null.
 * Split out so the latch logic is testable without DOM keyboard state.
 */
export function hotbarEdgeSlot(
  prev: readonly boolean[],
  down: readonly boolean[]
): number | null {
  for (let i = 0; i < down.length; i++) {
    if (down[i] && !(prev[i] ?? false)) return i;
  }
  return null;
}

/** Replace the hotbar contents. Call again whenever icons/counts change. */
export function setHotbarSlots(state: State, slots: HotbarSlotSpec[]): void {
  const data = ensureHotbarData(state);
  data.slots = slots;
  data.version++;
  if (data.active >= slots.length) data.active = Math.max(0, slots.length - 1);
}

/** Currently highlighted slot index (0 when empty). */
export function getHotbarActive(state: State): number {
  return ensureHotbarData(state).active;
}

/**
 * Move the highlight programmatically. Listeners do NOT fire — they hear
 * player interaction (key press / click) only, so tool selection can't
 * echo back into itself.
 */
export function setHotbarActive(state: State, index: number): void {
  const data = ensureHotbarData(state);
  if (index < 0 || index >= data.slots.length) return;
  data.active = index;
}

/** Subscribe to player slot activation. Returns an unsubscribe function. */
export function onHotbarActivate(
  state: State,
  listener: HotbarActivateListener
): () => void {
  const data = ensureHotbarData(state);
  data.listeners.push(listener);
  return () => {
    const i = data.listeners.indexOf(listener);
    if (i >= 0) data.listeners.splice(i, 1);
  };
}

const CSS = `
.hud-hotbar { display:flex; gap:10px; pointer-events:none; z-index:12; }
.hud-hotbar .hud-hotbar-card { transition:transform 0.08s ease, box-shadow 0.08s ease, border-color 0.08s ease; cursor:pointer; }
.hud-hotbar .hud-hotbar-card--active {
  transform:translateY(-4px);
  box-shadow:0 0 0 2px rgba(255,244,180,0.55), 0 8px 22px rgba(0,0,0,0.4);
}
`;

export function createHotbarWidget(
  attributes: Record<string, XMLValue>,
  _state: State
): HudWidget {
  const position = readPosition(attributes);

  injectWidgetCss(CSS);

  const widget: HudWidget = {
    id: HOTBAR_WIDGET_TYPE,
    mount(layer: HTMLDivElement, state: State): ReturnType<HudWidget['mount']> {
      const data = ensureHotbarData(state);
      const root = document.createElement('div');
      root.className = 'hud-hotbar';
      applyPosition(root, position, 'bottom-center');
      layer.appendChild(root);

      let cards: HTMLDivElement[] = [];
      let builtVersion = -1;

      const activate = (index: number): void => {
        if (index < 0 || index >= data.slots.length) return;
        data.active = index;
        for (const listener of [...data.listeners]) listener(index);
      };

      const rebuild = (): void => {
        for (const card of cards) card.remove();
        cards = data.slots.map((slot, i) => {
          const card = createHudSlot({
            icon: slot.icon,
            label: slot.label,
            key: String(i + 1),
            color: slot.color,
            count: slot.count,
            size: 52,
            iconFontSize: 25,
            iconImgSize: 40,
          });
          card.root.classList.add('hud-hotbar-card');
          card.root.addEventListener('click', () => activate(i));
          root.appendChild(card.root);
          return card.root;
        });
        builtVersion = data.version;
      };

      const update = (): void => {
        if (builtVersion !== data.version) rebuild();
        for (let i = 0; i < cards.length; i++) {
          cards[i].classList.toggle(
            'hud-hotbar-card--active',
            i === data.active
          );
        }
        const down = HOTBAR_KEYS.map((code) => isKeyDown(code));
        const pressed = hotbarEdgeSlot(data.latch, down);
        data.latch = down;
        if (pressed !== null) activate(pressed);
      };

      update();
      return { root, update, unmount: () => root.remove() };
    },
  };

  return widget;
}

export const hotbarFactory: HudWidgetFactory = createHotbarWidget;

export const hotbarRecipe: Recipe = {
  name: HOTBAR_TAG,
  components: [],
  parserAttributes: ['position'],
  parserOwnsChildren: true,
};

export const hotbarParser: Parser = makeWidgetParser(hotbarFactory);

let factoryRegistered = false;

/** Idempotent factory registration (hud plugin initialize). */
export function registerHotbarWidgetFactory(): void {
  if (factoryRegistered) return;
  factoryRegistered = true;
  registerHudWidgetFactory(HOTBAR_WIDGET_TYPE, hotbarFactory);
}
