import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  HudScreenUpdateSystem,
  type HudWidget,
  type WidgetHandle,
  createHudScreenLayer,
  getHudScreenLayer,
  registerHudWidget,
  registerHudWidgetFactory,
  unregisterHudWidget,
} from '../../../src/plugins/hud/screen-layer';

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

describe('HudScreenLayer — creation', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('createHudScreenLayer appends a .vibe-hud-screen-layer div to document.body', () => {
    const before = document.querySelectorAll('.vibe-hud-screen-layer').length;
    const layer = createHudScreenLayer(state);
    const after = document.querySelectorAll('.vibe-hud-screen-layer').length;
    expect(after).toBe(before + 1);
    expect(layer).toBeInstanceOf(HTMLElement);
    expect(layer.className).toBe('vibe-hud-screen-layer');
  });

  it('layer has screen-space overlay CSS (position, pointer-events, z-index)', () => {
    const layer = createHudScreenLayer(state);
    const style = layer.style;
    expect(style.position).toBe('absolute');
    expect(style.top).toBe('0px');
    expect(style.left).toBe('0px');
    expect(style.width).toBe('100%');
    expect(style.height).toBe('100%');
    expect(style.pointerEvents).toBe('none');
    expect(style.zIndex).toBe('10');
  });

  it('createHudScreenLayer is idempotent — returns the same div for one State', () => {
    const a = createHudScreenLayer(state);
    const b = createHudScreenLayer(state);
    expect(a).toBe(b);
  });

  it('getHudScreenLayer returns the existing layer without creating a second one', () => {
    const created = createHudScreenLayer(state);
    const fetched = getHudScreenLayer(state);
    expect(fetched).toBe(created);
  });

  it('getHudScreenLayer auto-creates the layer if initialize was not called', () => {
    const fresh = new State();
    const layer = getHudScreenLayer(fresh);
    expect(layer).toBeInstanceOf(HTMLElement);
    expect(layer.className).toBe('vibe-hud-screen-layer');
  });

  it('different States get different layers (WeakMap keyed by State)', () => {
    const other = newState();
    const layerA = getHudScreenLayer(state);
    const layerB = getHudScreenLayer(other);
    expect(layerA).not.toBe(layerB);
  });
});

describe('HudScreenLayer — HudPlugin.initialize', () => {
  it('HudPlugin.initialize creates the layer when not headless', async () => {
    const state = new State();
    state.registerPlugin(HudPlugin);
    expect(state.headless).toBe(false);
    await state.initializePlugins();
    const layer = getHudScreenLayer(state);
    expect(layer.className).toBe('vibe-hud-screen-layer');
    expect(layer.parentElement).toBe(document.body);
  });

  it('HudPlugin.initialize does not append a new layer when headless', async () => {
    const state = new State();
    state.headless = true;
    state.registerPlugin(HudPlugin);
    const before = document.querySelectorAll('.vibe-hud-screen-layer').length;
    await state.initializePlugins();
    const after = document.querySelectorAll('.vibe-hud-screen-layer').length;
    expect(after).toBe(before);
  });
});

describe('registerHudWidget — lifecycle', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(() => {
    state = newState();
    layer = getHudScreenLayer(state);
  });

  function makeMockWidget(id: string): {
    widget: HudWidget;
    counts: { mounts: number; updates: number; unmounts: number };
    handle: WidgetHandle;
  } {
    const counts = { mounts: 0, updates: 0, unmounts: 0 };
    const root = document.createElement('div');
    root.className = `widget-${id}`;
    const handle: WidgetHandle = {
      root,
      update: (s: State) => {
        counts.updates += 1;
        void s;
      },
      unmount: () => {
        counts.unmounts += 1;
      },
    };
    const widget: HudWidget = {
      id,
      mount: (l: HTMLDivElement, s: State) => {
        counts.mounts += 1;
        l.appendChild(root);
        void s;
        return handle;
      },
    };
    return { widget, counts, handle };
  }

  it('registerHudWidget calls widget.mount(layer, state) and attaches root to layer', () => {
    const mock = makeMockWidget('hp');
    registerHudWidget(state, mock.widget);
    expect(mock.counts.mounts).toBe(1);
    expect(layer.querySelector('.widget-hp')).not.toBeNull();
  });

  it('registerHudWidget ignores duplicate id (no second mount)', () => {
    const mock = makeMockWidget('hp');
    registerHudWidget(state, mock.widget);
    registerHudWidget(state, mock.widget);
    expect(mock.counts.mounts).toBe(1);
  });

  it('HudScreenUpdateSystem (group late) calls update() on every mounted widget each frame', () => {
    const mock = makeMockWidget('hp');
    registerHudWidget(state, mock.widget);
    expect(mock.counts.updates).toBe(0);
    HudScreenUpdateSystem.update!(state);
    expect(mock.counts.updates).toBe(1);
    HudScreenUpdateSystem.update!(state);
    expect(mock.counts.updates).toBe(2);
  });

  it('update() is optional — widgets without update are skipped silently', () => {
    const root = document.createElement('div');
    const widget: HudWidget = {
      id: 'static',
      mount: () => ({ root, unmount: () => {} }),
    };
    registerHudWidget(state, widget);
    expect(() => HudScreenUpdateSystem.update!(state)).not.toThrow();
  });

  it('unregisterHudWidget calls handle.unmount and stops update ticks', () => {
    const mock = makeMockWidget('hp');
    registerHudWidget(state, mock.widget);
    expect(mock.counts.unmounts).toBe(0);
    unregisterHudWidget(state, 'hp');
    expect(mock.counts.unmounts).toBe(1);
    const before = mock.counts.updates;
    HudScreenUpdateSystem.update!(state);
    expect(mock.counts.updates).toBe(before);
  });

  it('unregisterHudWidget is a no-op for unknown id', () => {
    expect(() => unregisterHudWidget(state, 'does-not-exist')).not.toThrow();
  });

  it('HudScreenUpdateSystem is in the "late" group', () => {
    expect(HudScreenUpdateSystem.group).toBe('late');
  });

  it('full step() cycle: step state → update fires', () => {
    const mock = makeMockWidget('xp');
    registerHudWidget(state, mock.widget);
    expect(mock.counts.updates).toBe(0);
    state.step(0.016);
    expect(mock.counts.updates).toBe(1);
    state.step(0.016);
    expect(mock.counts.updates).toBe(2);
  });
});

describe('registerHudWidgetFactory — XML parser hook', () => {
  it('registerHudWidgetFactory stores a factory retrievable for the parser', () => {
    const factory = () => ({
      id: 'test',
      mount: () => ({ root: document.createElement('div'), unmount: () => {} }),
    });
    registerHudWidgetFactory('test-kind', factory);
    expect(() => registerHudWidgetFactory('test-kind', factory)).not.toThrow();
  });
});

describe('HudScreenUpdateSystem — dispose cleans up the layer', () => {
  it('dispose removes the layer from the DOM and clears widget state', () => {
    const state = newState();
    const layer = getHudScreenLayer(state);
    expect(layer.parentElement).not.toBeNull();
    HudScreenUpdateSystem.dispose?.(state);
    expect(layer.parentElement).toBeNull();
    expect(() => HudScreenUpdateSystem.update!(state)).not.toThrow();
  });
});

describe('HudPlugin registration surface', () => {
  it('HudPlugin now has 3 systems (HudBuild, HudSync, HudScreenUpdate)', () => {
    expect(HudPlugin.systems).toHaveLength(3);
  });

  it('HudPlugin has a HudScreenLayer recipe', () => {
    const recipe = HudPlugin.recipes?.find((r) => r.name === 'HudScreenLayer');
    expect(recipe).toBeDefined();
  });

  it('HudPlugin has a HudWidget recipe', () => {
    const recipe = HudPlugin.recipes?.find((r) => r.name === 'HudWidget');
    expect(recipe).toBeDefined();
  });

  it('HudPlugin has parsers for HudScreenLayer and HudWidget', () => {
    expect(HudPlugin.config?.parsers?.HudScreenLayer).toBeDefined();
    expect(HudPlugin.config?.parsers?.HudWidget).toBeDefined();
  });

  it('HudPlugin.initialize is defined', () => {
    expect(HudPlugin.initialize).toBeDefined();
  });
});
