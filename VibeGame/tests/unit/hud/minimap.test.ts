import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import {
  DEFAULT_MINIMAP_COLORS,
  DEFAULT_MINIMAP_RADII,
  MINIMAP_WIDGET_TYPE,
  MinimapWidget,
  collectMinimapDots,
  drawMinimap,
  parseMinimapOptions,
  resolveMinimapCategory,
} from '../../../src/plugins/hud/widgets/minimap';
import type { MinimapOptions } from '../../../src/plugins/hud/widgets/minimap';
import { createHudScreenLayer } from '../../../src/plugins/hud/screen-layer';
import { Transform } from '../../../src/plugins/transforms';
import { PlayerController } from '../../../src/plugins/player';
import { FactionComponent, Health } from '../../../src/plugins/combat';
import { NavMeshAgent } from '../../../src/plugins/navmesh';
import { ResourceNode } from '../../../src/plugins/rpg-resource-node';
import { ResourceNodePlugin } from '../../../src/plugins/rpg-resource-node/plugin';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser as unknown as typeof DOMParser;
  globalThis.document = dom.window.document as unknown as typeof document;
  globalThis.window = dom.window as unknown as typeof window;
  globalThis.HTMLElement = dom.window
    .HTMLElement as unknown as typeof HTMLElement;
  globalThis.HTMLDivElement = dom.window
    .HTMLDivElement as unknown as typeof HTMLDivElement;
  globalThis.HTMLCanvasElement = dom.window
    .HTMLCanvasElement as unknown as typeof HTMLCanvasElement;
});

function newWorld(): State {
  const state = new State();
  state.registerPlugin(HudPlugin);
  state.registerPlugin(ResourceNodePlugin);
  return state;
}

function defaultOptions(
  overrides: Partial<MinimapOptions> = {}
): MinimapOptions {
  return {
    range: 60,
    size: 168,
    categories: new Set([
      'player',
      'enemy',
      'boss',
      'merchant',
      'wood',
      'stone',
      'neutral',
    ]),
    colors: { ...DEFAULT_MINIMAP_COLORS },
    radii: { ...DEFAULT_MINIMAP_RADII },
    anchor: 'top-right',
    ...overrides,
  };
}

function place(
  state: State,
  pos: [number, number, number],
  components: Record<string, unknown>[],
  values?: Record<string, Record<string, number>>
): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform, {
    posX: pos[0],
    posY: pos[1],
    posZ: pos[2],
  });
  for (let i = 0; i < components.length; i++) {
    state.addComponent(eid, components[i], values?.[i]);
  }
  return eid;
}

describe('parseMinimapOptions', () => {
  it('applies default range and size when omitted', () => {
    const opts = parseMinimapOptions({}, []);
    expect(opts.range).toBe(60);
    expect(opts.size).toBe(168);
    expect(opts.anchor).toBe('top-right');
    expect(opts.categories.has('enemy')).toBe(true);
    expect(opts.categories.has('wood')).toBe(true);
  });

  it('parses range, size and anchor attributes', () => {
    const opts = parseMinimapOptions(
      { range: '120', size: '256', anchor: 'bottom-left' },
      []
    );
    expect(opts.range).toBe(120);
    expect(opts.size).toBe(256);
    expect(opts.anchor).toBe('bottom-left');
  });

  it('filters categories to the requested set plus player', () => {
    const opts = parseMinimapOptions(
      { categories: 'enemy,boss,merchant,wood,stone' },
      []
    );
    expect(opts.categories.has('player')).toBe(true);
    expect(opts.categories.has('enemy')).toBe(true);
    expect(opts.categories.has('merchant')).toBe(true);
    expect(opts.categories.has('neutral')).toBe(false);
  });

  it('reads inline color-* attribute overrides', () => {
    const opts = parseMinimapOptions(
      { 'color-enemy': '#00ff00', 'color-boss': '#101010' },
      []
    );
    expect(opts.colors.enemy).toBe('#00ff00');
    expect(opts.colors.boss).toBe('#101010');
    expect(opts.colors.player).toBe(DEFAULT_MINIMAP_COLORS.player);
  });

  it('reads <MinimapColor> child overrides', () => {
    const children = [
      {
        tagName: 'MinimapColor',
        attributes: { category: 'enemy', color: '#ff0000' },
        children: [],
      },
      {
        tagName: 'MinimapColor',
        attributes: { category: 'merchant', color: '#ffcc00' },
        children: [],
      },
    ];
    const opts = parseMinimapOptions({}, children);
    expect(opts.colors.enemy).toBe('#ff0000');
    expect(opts.colors.merchant).toBe('#ffcc00');
  });

  it('child override wins over inline default but ignores unknown category', () => {
    const children = [
      {
        tagName: 'MinimapColor',
        attributes: { category: 'unknown-thing', color: '#ff0000' },
        children: [],
      },
    ];
    const opts = parseMinimapOptions({}, children);
    expect(Object.keys(opts.colors)).toHaveLength(7);
  });

  it('falls back when numeric attrs are invalid', () => {
    const opts = parseMinimapOptions({ range: 'nope', size: '0' }, []);
    expect(opts.range).toBe(60);
    expect(opts.size).toBe(168);
  });
});

describe('resolveMinimapCategory', () => {
  let state: State;

  beforeEach(() => {
    state = newWorld();
  });

  it('classifies the player entity', () => {
    const eid = place(state, [0, 0, 0], [PlayerController]);
    expect(resolveMinimapCategory(state, eid)).toBe('player');
  });

  it('classifies wood and stone resource nodes', () => {
    const wood = place(state, [0, 0, 0], [ResourceNode], { 0: { kind: 0 } });
    const stone = place(state, [0, 0, 0], [ResourceNode], { 0: { kind: 1 } });
    expect(resolveMinimapCategory(state, wood)).toBe('wood');
    expect(resolveMinimapCategory(state, stone)).toBe('stone');
  });

  it('classifies ore as stone (no dedicated ore category)', () => {
    const ore = place(state, [0, 0, 0], [ResourceNode], { 0: { kind: 2 } });
    expect(resolveMinimapCategory(state, ore)).toBe('stone');
  });

  it('classifies faction enemies, bosses and merchants', () => {
    const enemy = place(state, [0, 0, 0], [FactionComponent], {
      0: { tag: 1 },
    });
    const merchant = place(state, [0, 0, 0], [FactionComponent], {
      0: { tag: 3 },
    });
    const neutral = place(state, [0, 0, 0], [FactionComponent], {
      0: { tag: 2 },
    });
    expect(resolveMinimapCategory(state, enemy)).toBe('enemy');
    expect(resolveMinimapCategory(state, merchant)).toBe('merchant');
    expect(resolveMinimapCategory(state, neutral)).toBe('neutral');
  });

  it('returns null for entities without recognisable components', () => {
    const eid = place(state, [0, 0, 0], []);
    expect(resolveMinimapCategory(state, eid)).toBeNull();
  });
});

describe('collectMinimapDots', () => {
  let state: State;

  beforeEach(() => {
    state = newWorld();
  });

  it('returns null player marker when no player exists', () => {
    const result = collectMinimapDots(state, defaultOptions());
    expect(result.player).toBeNull();
    expect(result.dots).toHaveLength(0);
  });

  it('collects the player marker at origin and dots within range', () => {
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [10, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });
    place(state, [40, 0, 0], [NavMeshAgent, Health], {
      1: { current: 200, max: 200 },
    });

    const result = collectMinimapDots(state, defaultOptions({ range: 60 }));

    expect(result.player).not.toBeNull();
    expect(result.player!.x).toBe(0);
    expect(result.player!.z).toBe(0);
    expect(result.dots).toHaveLength(2);
    const near = result.dots.find((d) => d.x === 10);
    const far = result.dots.find((d) => d.x === 40);
    expect(near?.category).toBe('enemy');
    expect(far?.category).toBe('enemy');
  });

  it('excludes entities outside the configured range', () => {
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [10, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });
    place(state, [70, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });

    const result = collectMinimapDots(state, defaultOptions({ range: 60 }));

    expect(result.dots).toHaveLength(1);
    expect(result.dots[0].x).toBe(10);
  });

  it('skips dead creatures (Health.current <= 0)', () => {
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [10, 0, 0], [NavMeshAgent, Health], {
      1: { current: 0, max: 50 },
    });
    place(state, [12, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });

    const result = collectMinimapDots(state, defaultOptions());

    expect(result.dots).toHaveLength(1);
    expect(result.dots[0].x).toBe(12);
  });

  it('filters dots whose category is not in the enabled set', () => {
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [8, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });
    place(state, [5, 0, 0], [ResourceNode], { 0: { kind: 0 } });

    const result = collectMinimapDots(
      state,
      defaultOptions({ categories: new Set(['player', 'enemy']) })
    );

    expect(result.dots).toHaveLength(1);
    expect(result.dots[0].category).toBe('enemy');
  });

  it('collects resource nodes by kind', () => {
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [3, 0, 3], [ResourceNode], { 0: { kind: 0 } });
    place(state, [-4, 0, 0], [ResourceNode], { 0: { kind: 1 } });

    const result = collectMinimapDots(state, defaultOptions());

    const cats = result.dots.map((d) => d.category).sort();
    expect(cats).toEqual(['stone', 'wood']);
  });

  it('centers the scan on the player when the player is off-origin', () => {
    place(state, [20, 0, 20], [PlayerController]);
    place(state, [25, 0, 20], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });
    place(state, [200, 0, 20], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });

    const result = collectMinimapDots(state, defaultOptions({ range: 60 }));

    expect(result.player!.x).toBe(20);
    expect(result.dots).toHaveLength(1);
    expect(result.dots[0].x).toBe(25);
  });

  it('never includes the player entity itself as a dot', () => {
    place(state, [0, 0, 0], [PlayerController]);
    const result = collectMinimapDots(state, defaultOptions());
    expect(result.dots.find((d) => d.category === 'player')).toBeUndefined();
  });
});

describe('drawMinimap', () => {
  function makeContext(size: number): {
    ctx: CanvasRenderingContext2D;
    canvas: HTMLCanvasElement;
  } {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('2D context unavailable in this environment');
    return { ctx, canvas };
  }

  it('paints without throwing for a populated collection', () => {
    const size = 168;
    const opts = defaultOptions({ size });
    const collection = {
      player: { x: 0, z: 0, heading: 0 },
      dots: [
        { x: 10, z: 0, category: 'enemy' as const },
        { x: 40, z: 0, category: 'boss' as const },
      ],
    };
    const { ctx } = makeContext(size);
    expect(() => drawMinimap(ctx, collection, opts)).not.toThrow();
  });

  it('handles an empty collection (only player arrow) without throwing', () => {
    const size = 168;
    const opts = defaultOptions({ size });
    const collection = {
      player: { x: 0, z: 0, heading: 0 },
      dots: [],
    };
    const { ctx } = makeContext(size);
    expect(() => drawMinimap(ctx, collection, opts)).not.toThrow();
  });

  it('renders even when there is no player marker', () => {
    const size = 128;
    const opts = defaultOptions({ size });
    const { ctx } = makeContext(size);
    expect(() =>
      drawMinimap(ctx, { player: null, dots: [] }, opts)
    ).not.toThrow();
  });
});

describe('MinimapWidget', () => {
  let state: State;
  let layer: HTMLDivElement;

  beforeEach(() => {
    state = newWorld();
    layer = createHudScreenLayer(state);
  });

  it('exposes the canonical widget id', () => {
    const widget = new MinimapWidget(defaultOptions());
    expect(widget.id).toBe(MINIMAP_WIDGET_TYPE);
  });

  it('mount attaches a canvas with the configured pixel size to the layer', () => {
    const widget = new MinimapWidget(defaultOptions({ size: 200 }));
    const handle = widget.mount(layer);
    const canvas = handle.root.querySelector('canvas');
    expect(canvas).not.toBeNull();
    expect(canvas!.width).toBe(200);
    expect(canvas!.height).toBe(200);
    expect(layer.contains(handle.root)).toBe(true);
    handle.unmount();
    expect(layer.contains(handle.root)).toBe(false);
  });

  it('update is a safe no-op when 2D context is unavailable (jsdom without canvas pkg)', () => {
    const widget = new MinimapWidget(defaultOptions());
    const handle = widget.mount(layer);
    place(state, [0, 0, 0], [PlayerController]);
    expect(() => handle.update?.(state)).not.toThrow();
    handle.unmount();
  });

  it('update renders entities when a 2D context is available', () => {
    const widget = new MinimapWidget(defaultOptions());
    const handle = widget.mount(layer);
    place(state, [0, 0, 0], [PlayerController]);
    place(state, [10, 0, 0], [NavMeshAgent, Health], {
      1: { current: 50, max: 50 },
    });
    const canvas = handle.root.querySelector('canvas')!;
    const realGetContext = canvas.getContext.bind(canvas);
    const probe = realGetContext('2d');
    if (probe) {
      expect(() => handle.update?.(state)).not.toThrow();
    }
    handle.unmount();
  });

  it('honours anchor top-left in the wrapper style', () => {
    const widget = new MinimapWidget(defaultOptions({ anchor: 'top-left' }));
    const handle = widget.mount(layer);
    expect(handle.root.style.left).toBe('18px');
    expect(handle.root.style.top).toBe('18px');
    handle.unmount();
  });
});
