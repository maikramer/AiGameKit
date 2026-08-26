import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  HudScreenUpdateSystem,
  getHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import {
  createHotbarWidget,
  getHotbarActive,
  hotbarEdgeSlot,
  onHotbarActivate,
  setHotbarActive,
  setHotbarSlots,
  type HotbarSlotSpec,
} from '../../../src/plugins/hud/widgets/hotbar';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window
    .HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window
    .HTMLDivElement as unknown as typeof HTMLDivElement;
});

const SLOTS: HotbarSlotSpec[] = [
  { icon: ' Hoe', label: 'Hoe', color: '#c68d54' },
  { icon: '💧', label: 'Watering can', color: '#5ab7ff' },
  { icon: '🥕', label: 'Turnip seeds', color: '#ffd166', count: 4 },
];

function newState(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  return state;
}

/** Mount the hotbar widget, flush one update frame, return the rendered cards. */
function mountHotbar(state: State): HTMLElement[] {
  registerHudWidget(state, createHotbarWidget({}, state));
  HudScreenUpdateSystem.update!(state);
  return [
    ...getHudScreenLayer(state).querySelectorAll('.hud-hotbar-card'),
  ] as HTMLElement[];
}

describe('hotbarEdgeSlot — edge-triggered key latch', () => {
  it('returns the index of a freshly pressed key', () => {
    expect(hotbarEdgeSlot([false, false, false], [false, true, false])).toBe(1);
  });

  it('returns null while the key stays held', () => {
    expect(
      hotbarEdgeSlot([false, true, false], [false, true, false])
    ).toBeNull();
  });

  it('re-activates after a release', () => {
    expect(hotbarEdgeSlot([true], [false])).toBeNull();
    expect(hotbarEdgeSlot([false], [true])).toBe(0);
  });

  it('picks the first of several simultaneous fresh presses', () => {
    expect(hotbarEdgeSlot([false, false], [true, true])).toBe(0);
  });
});

describe('hotbar state API', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('active slot starts at 0', () => {
    expect(getHotbarActive(state)).toBe(0);
  });

  it('setHotbarActive moves within bounds and ignores out-of-range', () => {
    setHotbarSlots(state, SLOTS);
    setHotbarActive(state, 2);
    expect(getHotbarActive(state)).toBe(2);
    setHotbarActive(state, 5);
    expect(getHotbarActive(state)).toBe(2);
    setHotbarActive(state, -1);
    expect(getHotbarActive(state)).toBe(2);
  });

  it('shrinking the slots clamps the active index', () => {
    setHotbarSlots(state, SLOTS);
    setHotbarActive(state, 2);
    setHotbarSlots(state, SLOTS.slice(0, 1));
    expect(getHotbarActive(state)).toBe(0);
  });
});

describe('hotbar widget rendering', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('renders one card per slot with key badges 1..n and the count badge', () => {
    setHotbarSlots(state, SLOTS);
    const cards = mountHotbar(state);
    expect(cards.length).toBe(3);
    expect(cards[0].title).toBe('[1] Hoe');
    expect(cards[2].querySelectorAll('span')[0].textContent).toBe('3');
    // The third slot has count 4 → its bottom-right badge is visible with "4".
    const badges = cards[2].querySelectorAll('span');
    const countBadge = badges[badges.length - 1];
    expect(countBadge.textContent).toBe('4');
  });

  it('marks the active card and follows setHotbarActive on the next frame', () => {
    setHotbarSlots(state, SLOTS);
    const cards = mountHotbar(state);
    expect(cards[0].classList.contains('hud-hotbar-card--active')).toBeTrue();

    setHotbarActive(state, 1);
    HudScreenUpdateSystem.update!(state);
    expect(cards[1].classList.contains('hud-hotbar-card--active')).toBeTrue();
    expect(cards[0].classList.contains('hud-hotbar-card--active')).toBeFalse();
  });

  it('rebuilds the row when setHotbarSlots is called again', () => {
    setHotbarSlots(state, SLOTS);
    const layer = getHudScreenLayer(state);
    registerHudWidget(state, createHotbarWidget({}, state));
    HudScreenUpdateSystem.update!(state);
    setHotbarSlots(state, SLOTS.slice(0, 2));
    HudScreenUpdateSystem.update!(state);
    expect(layer.querySelectorAll('.hud-hotbar-card').length).toBe(2);
  });
});

describe('hotbar activation listeners', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('clicking a card activates it and fires listeners with the index', () => {
    setHotbarSlots(state, SLOTS);
    const cards = mountHotbar(state);
    const calls: number[] = [];
    onHotbarActivate(state, (i) => calls.push(i));

    (cards[2] as HTMLDivElement).click();
    expect(calls).toEqual([2]);
    expect(getHotbarActive(state)).toBe(2);
  });

  it('unsubscribed listeners stop hearing activations', () => {
    setHotbarSlots(state, SLOTS);
    const cards = mountHotbar(state);
    const calls: number[] = [];
    const off = onHotbarActivate(state, (i) => calls.push(i));

    (cards[0] as HTMLDivElement).click();
    off();
    (cards[0] as HTMLDivElement).click();
    expect(calls.length).toBe(1);
  });

  it('programmatic setHotbarActive does not fire listeners', () => {
    setHotbarSlots(state, SLOTS);
    mountHotbar(state);
    const calls: number[] = [];
    onHotbarActivate(state, (i) => calls.push(i));

    setHotbarActive(state, 1);
    expect(calls).toBeEmpty();
  });
});
