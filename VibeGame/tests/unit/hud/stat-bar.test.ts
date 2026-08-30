import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'aigamekit-vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  HudScreenUpdateSystem,
  getHudScreenLayer,
  registerHudWidget,
} from '../../../src/plugins/hud/screen-layer';
import {
  createStatBarWidget,
  getHudStatSource,
  registerHudStatSource,
} from '../../../src/plugins/hud/widgets/stat-bar';

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

function newState(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  return state;
}

function mountStatBar(state: State, stat = 'stamina'): HTMLElement {
  registerHudWidget(
    state,
    createStatBarWidget(
      { stat, icon: '⚡', color: '#ffd166', label: 'Stamina' },
      state
    )
  );
  HudScreenUpdateSystem.update!(state);
  return getHudScreenLayer(state).querySelector('.hud-statbar') as HTMLElement;
}

function fill(root: HTMLElement): HTMLElement {
  return root.querySelector('.hud-statbar-fill') as HTMLElement;
}

function text(root: HTMLElement): string {
  return root.querySelector('.hud-statbar-text')?.textContent ?? '';
}

describe('stat source registry', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('registerHudStatSource stores and replaces the source', () => {
    registerHudStatSource(state, 'stamina', () => ({ cur: 1, max: 2 }));
    expect(getHudStatSource(state, 'stamina')?.()).toEqual({ cur: 1, max: 2 });

    registerHudStatSource(state, 'stamina', () => ({ cur: 3, max: 4 }));
    expect(getHudStatSource(state, 'stamina')?.()).toEqual({ cur: 3, max: 4 });
  });

  it('the unregister function removes the source', () => {
    const off = registerHudStatSource(state, 'stamina', () => ({
      cur: 1,
      max: 2,
    }));
    off();
    expect(getHudStatSource(state, 'stamina')).toBeUndefined();
  });

  it('sources are per-state', () => {
    const other = newState();
    registerHudStatSource(state, 'stamina', () => ({ cur: 1, max: 2 }));
    expect(getHudStatSource(other, 'stamina')).toBeUndefined();
  });
});

describe('stat-bar widget rendering', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('renders cur/max with a proportional fill width', () => {
    registerHudStatSource(state, 'stamina', () => ({ cur: 67, max: 100 }));
    const bar = mountStatBar(state);

    expect(text(bar)).toBe('67/100');
    expect(parseFloat(fill(bar).style.width)).toBeCloseTo(67, 5);
    expect(bar.classList.contains('hud-statbar--low')).toBeFalse();
  });

  it('clamps out-of-range values into 0..100%', () => {
    registerHudStatSource(state, 'stamina', () => ({ cur: 150, max: 100 }));
    const bar = mountStatBar(state);
    expect(parseFloat(fill(bar).style.width)).toBe(100);

    registerHudStatSource(state, 'stamina', () => ({ cur: -5, max: 100 }));
    HudScreenUpdateSystem.update!(state);
    expect(parseFloat(fill(bar).style.width)).toBe(0);
  });

  it('flags the low state at or below 25%', () => {
    registerHudStatSource(state, 'stamina', () => ({ cur: 20, max: 100 }));
    const bar = mountStatBar(state);
    expect(bar.classList.contains('hud-statbar--low')).toBeTrue();
  });

  it('shows a placeholder while no source is registered (or max is 0)', () => {
    const bar = mountStatBar(state);
    expect(text(bar)).toBe('–/–');
    expect(fill(bar).style.width).toBe('0%');

    registerHudStatSource(state, 'stamina', () => ({ cur: 5, max: 0 }));
    HudScreenUpdateSystem.update!(state);
    expect(text(bar)).toBe('–/–');
  });

  it('tracks the live source value across frames', () => {
    let cur = 100;
    registerHudStatSource(state, 'stamina', () => ({ cur, max: 100 }));
    const bar = mountStatBar(state);
    expect(text(bar)).toBe('100/100');

    cur = 42;
    HudScreenUpdateSystem.update!(state);
    expect(text(bar)).toBe('42/100');
    expect(parseFloat(fill(bar).style.width)).toBeCloseTo(42, 5);
  });
});
