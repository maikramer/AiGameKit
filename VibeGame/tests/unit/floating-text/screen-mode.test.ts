import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { FloatingTextPlugin } from '../../../src/plugins/floating-text/plugin';
import { FloatingText } from '../../../src/plugins/floating-text/components';
import {
  FloatingTextScreenUpdateSystem,
  FloatingTextUpdateSystem,
} from '../../../src/plugins/floating-text/systems';
import {
  disposeScreenFloatPool,
  getFloatingScreenPoolSize,
  getScreenFloatPool,
} from '../../../src/plugins/floating-text/screen-pool';
import {
  deleteFloatingTextString,
  spawnFloatingText,
  spawnFloatingTextScreen,
} from '../../../src/plugins/floating-text/utils';
import { getHudScreenLayer } from '../../../src/plugins/hud/screen-layer';
import { Transform } from '../../../src/plugins/transforms/components';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window.HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window.HTMLDivElement as unknown as typeof HTMLDivElement;
  globalThis.HTMLSpanElement = dom.window.HTMLSpanElement as unknown as typeof HTMLSpanElement;
});

function newState(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  state.registerPlugin(FloatingTextPlugin);
  return state;
}

describe('FloatingTextPlugin — registration surface', () => {
  it('FloatingTextPlugin registers both world and screen systems', () => {
    expect(FloatingTextPlugin.systems).toContain(FloatingTextUpdateSystem);
    expect(FloatingTextPlugin.systems).toContain(FloatingTextScreenUpdateSystem);
  });

  it('FloatingText component has screen-space SOA fields', () => {
    expect(FloatingText.space).toBeDefined();
    expect(FloatingText.screenX).toBeDefined();
    expect(FloatingText.screenY).toBeDefined();
    expect(FloatingText.fontSizePx).toBeDefined();
    expect(FloatingText.driftX).toBeDefined();
    expect(FloatingText.crit).toBeDefined();
  });

  it('FloatingTextScreenUpdateSystem runs in the "late" group', () => {
    expect(FloatingTextScreenUpdateSystem.group).toBe('late');
  });

  it('FloatingTextUpdateSystem (world) still runs in the "draw" group', () => {
    expect(FloatingTextUpdateSystem.group).toBe('draw');
  });
});

describe('spawnFloatingText — screen mode (opts.space === "screen")', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('marks the entity with space=1 (screen) and skips Transform', () => {
    const eid = spawnFloatingText(state, '+25', {
      space: 'screen',
      x: 100,
      y: 200,
      color: '#ffffff',
    });
    expect(FloatingText.space[eid]).toBe(1);
    expect(FloatingText.screenX[eid]).toBe(100);
    expect(FloatingText.screenY[eid]).toBe(200);
    expect(state.hasComponent(eid, Transform)).toBe(false);
  });

  it('parses "#rrggbb" color strings into R/G/B floats', () => {
    const eid = spawnFloatingText(state, 'x', {
      space: 'screen',
      x: 0,
      y: 0,
      color: '#ff8800',
    });
    expect(FloatingText.colorR[eid]).toBeCloseTo(1, 1);
    expect(FloatingText.colorG[eid]).toBeCloseTo(0x88 / 255, 2);
    expect(FloatingText.colorB[eid]).toBeCloseTo(0, 1);
  });

  it('parses 3-digit "#rgb" shorthand', () => {
    const eid = spawnFloatingText(state, 'x', {
      space: 'screen',
      x: 0,
      y: 0,
      color: '#f80',
    });
    expect(FloatingText.colorR[eid]).toBeCloseTo(1, 1);
    expect(FloatingText.colorG[eid]).toBeCloseTo(0x88 / 255, 2);
    expect(FloatingText.colorB[eid]).toBeCloseTo(0, 1);
  });

  it('still accepts numeric 0xRRGGBB colors (backwards compat)', () => {
    const eid = spawnFloatingText(state, 'x', {
      space: 'screen',
      x: 0,
      y: 0,
      color: 0xff8800,
    });
    expect(FloatingText.colorR[eid]).toBeCloseTo(1, 1);
    expect(FloatingText.colorG[eid]).toBeCloseTo(0x88 / 255, 2);
  });

  it('persists the string payload in the sidecar map', () => {
    const eid = spawnFloatingText(state, 'CRIT!', {
      space: 'screen',
      x: 10,
      y: 20,
    });
    expect(state.exists(eid)).toBe(true);
  });

  it('honors crit flag (crit=1) and explicit fontSizePx', () => {
    const eid = spawnFloatingText(state, '42!', {
      space: 'screen',
      x: 0,
      y: 0,
      crit: true,
      fontSizePx: 28,
    });
    expect(FloatingText.crit[eid]).toBe(1);
    expect(FloatingText.fontSizePx[eid]).toBe(28);
  });

  it('uses default riseSpeed=50 (px/s) when not specified', () => {
    const eid = spawnFloatingText(state, 'x', {
      space: 'screen',
      x: 0,
      y: 0,
    });
    expect(FloatingText.riseSpeed[eid]).toBe(50);
  });

  it('assigns a bounded random driftX in [-17, 17] when omitted', () => {
    for (let i = 0; i < 16; i++) {
      const eid = spawnFloatingText(state, 'x', {
        space: 'screen',
        x: 0,
        y: 0,
      });
      const d = FloatingText.driftX[eid];
      expect(d).toBeGreaterThanOrEqual(-17);
      expect(d).toBeLessThanOrEqual(17);
    }
  });

  it('does not require z (screen mode)', () => {
    expect(() =>
      spawnFloatingText(state, 'ok', { space: 'screen', x: 1, y: 1 })
    ).not.toThrow();
  });
});

describe('spawnFloatingTextScreen — convenience wrapper', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('produces a space=1 entity equivalent to spawnFloatingText(space:"screen")', () => {
    const eid = spawnFloatingTextScreen(state, '+10 HP', {
      x: 320,
      y: 180,
      color: '#7CFC00',
    });
    expect(FloatingText.space[eid]).toBe(1);
    expect(FloatingText.screenX[eid]).toBe(320);
    expect(FloatingText.screenY[eid]).toBe(180);
    expect(state.exists(eid)).toBe(true);
  });

  it(' forwards crit/duration/fontSizePx', () => {
    const eid = spawnFloatingTextScreen(state, 'big', {
      x: 0,
      y: 0,
      crit: true,
      duration: 2.5,
      fontSizePx: 30,
    });
    expect(FloatingText.crit[eid]).toBe(1);
    expect(FloatingText.duration[eid]).toBe(2.5);
    expect(FloatingText.fontSizePx[eid]).toBe(30);
  });
});

describe('ScreenFloatPool — DOM recycling inside HudScreenLayer', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('lazy-creates a pool of span.vibe-float-screen inside the HUD layer on first access', () => {
    const layer = getHudScreenLayer(state);
    const before = layer.querySelectorAll('.vibe-float-screen').length;
    const pool = getScreenFloatPool(state, { size: 8 });
    const after = layer.querySelectorAll('.vibe-float-screen').length;
    expect(pool.size).toBe(8);
    expect(after).toBe(before + 8);
    expect(getFloatingScreenPoolSize(state)).toBe(8);
  });

  it('is idempotent — getScreenFloatPool returns the same instance for a State', () => {
    const a = getScreenFloatPool(state, { size: 12 });
    const b = getScreenFloatPool(state, { size: 32 });
    expect(a).toBe(b);
    expect(b.size).toBe(12);
  });

  it('applySpawn assigns a pool span with the text content + crit color', () => {
    const pool = getScreenFloatPool(state, { size: 4 });
    const eid = spawnFloatingTextScreen(state, 'CRIT', {
      x: 50,
      y: 60,
      color: '#ffffff',
      crit: true,
    });
    pool.applySpawn(state, eid);
    const entry = pool.getEntry(eid);
    expect(entry).toBeDefined();
    expect(entry!.el.textContent).toBe('CRIT');
    expect(entry!.el.style.color).toBe('rgb(255, 107, 61)');
  });

  it('releases the span back when the entity duration elapses (updateEntity)', () => {
    const pool = getScreenFloatPool(state, { size: 4 });
    const eid = spawnFloatingTextScreen(state, 'x', {
      x: 0,
      y: 0,
      duration: 1.0,
    });
    pool.applySpawn(state, eid);
    expect(pool.getEntry(eid)).toBeDefined();
    pool.updateEntity(eid, 1.5);
    expect(pool.getEntry(eid)).toBeUndefined();
  });

  it('recycles oldest entry when pool is exhausted (FIFO eviction)', () => {
    const pool = getScreenFloatPool(state, { size: 2 });
    const e1 = spawnFloatingTextScreen(state, 'a', { x: 0, y: 0 });
    const e2 = spawnFloatingTextScreen(state, 'b', { x: 0, y: 0 });
    const e3 = spawnFloatingTextScreen(state, 'c', { x: 0, y: 0 });
    pool.applySpawn(state, e1);
    pool.applySpawn(state, e2);
    expect(pool.getEntry(e1)).toBeDefined();
    expect(pool.getEntry(e2)).toBeDefined();
    pool.applySpawn(state, e3);
    expect(pool.getEntry(e3)).toBeDefined();
    expect(state.exists(e1)).toBe(false);
    expect(state.exists(e2)).toBe(true);
    expect(state.exists(e3)).toBe(true);
  });

  it('dispose removes all spans and clears the registry', () => {
    const layer = getHudScreenLayer(state);
    const pool = getScreenFloatPool(state, { size: 3 });
    expect(pool.size).toBe(3);
    disposeScreenFloatPool(state);
    expect(layer.querySelectorAll('.vibe-float-screen').length).toBe(0);
    expect(getFloatingScreenPoolSize(state)).toBe(0);
  });
});

describe('FloatingTextScreenUpdateSystem — full step cycle', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('mounts DOM spans for space=1 entities on the first step', () => {
    const eid = spawnFloatingTextScreen(state, '+25', {
      x: 100,
      y: 200,
      color: '#fff',
    });
    state.step(0.016);
    const layer = getHudScreenLayer(state);
    const active = Array.from(layer.querySelectorAll('.vibe-float-screen')).filter(
      (el) => (el as HTMLSpanElement).style.opacity !== '0'
    );
    expect(active.length).toBeGreaterThanOrEqual(1);
    expect(state.exists(eid)).toBe(true);
  });

  it('coexists with world-mode entities (regression: world is not touched by screen system)', () => {
    const worldEid = spawnFloatingText(state, 'world', {
      x: 0,
      y: 5,
      z: 0,
      color: 0xffffff,
    });
    const screenEid = spawnFloatingTextScreen(state, 'screen', {
      x: 50,
      y: 50,
    });
    expect(FloatingText.space[worldEid]).toBe(0);
    expect(FloatingText.space[screenEid]).toBe(1);
    state.step(0.016);
    expect(state.exists(worldEid)).toBe(true);
    expect(state.exists(screenEid)).toBe(true);
  });

  it('destroys the entity after duration elapses via repeated steps', () => {
    const eid = spawnFloatingTextScreen(state, 'ephemeral', {
      x: 0,
      y: 0,
      duration: 0.5,
    });
    for (let i = 0; i < 40; i++) state.step(0.016);
    expect(state.exists(eid)).toBe(false);
    deleteFloatingTextString(state, eid);
  });

  it('is a no-op when headless', () => {
    state.headless = true;
    const eid = spawnFloatingTextScreen(state, 'hidden', {
      x: 0,
      y: 0,
    });
    state.step(0.016);
    const layer = getHudScreenLayer(state);
    const visible = Array.from(layer.querySelectorAll('.vibe-float-screen')).filter(
      (el) => (el as HTMLSpanElement).style.opacity !== '0'
    );
    expect(visible.length).toBe(0);
    expect(state.exists(eid)).toBe(true);
  });

  it('dispose() removes the pool', () => {
    spawnFloatingTextScreen(state, '+1', { x: 1, y: 1 });
    state.step(0.016);
    expect(getFloatingScreenPoolSize(state)).toBeGreaterThan(0);
    FloatingTextScreenUpdateSystem.dispose?.(state);
    expect(getFloatingScreenPoolSize(state)).toBe(0);
  });
});

describe('spawnFloatingText — world mode backwards compatibility', () => {
  let state: State;

  beforeEach(() => {
    state = newState();
  });

  it('default space is world (space=0) when opts.space omitted', () => {
    const eid = spawnFloatingText(state, '+1 Pedra', {
      x: 1,
      y: 2,
      z: 3,
      color: 0xffd27a,
    });
    expect(FloatingText.space[eid]).toBe(0);
    expect(state.hasComponent(eid, Transform)).toBe(true);
  });

  it('world mode uses riseSpeed default 0.9 (m/s) and size 0.35 (m)', () => {
    const eid = spawnFloatingText(state, 'x', {
      x: 0,
      y: 0,
      z: 0,
    });
    expect(FloatingText.riseSpeed[eid]).toBeCloseTo(0.9, 2);
    expect(FloatingText.size[eid]).toBeCloseTo(0.35, 2);
  });
});
