import { afterEach, describe, expect, it, mock } from 'bun:test';
import { State } from 'vibegame';
import { DebugPlugin } from '../../../src/plugins/debug/plugin';

/**
 * The debug bridge must survive any plugin registration order: profiler/audio
 * attach `__VIBEGAME__.profiler` / `.audio` namespaces before or after
 * DebugPlugin initializes, and DebugPlugin merges instead of bailing when
 * another plugin created the object first.
 */
describe('DebugPlugin bridge merge', () => {
  const originalWindow = globalThis.window;

  afterEach(() => {
    (globalThis as Record<string, unknown>).window = originalWindow;
    if (originalWindow) {
      delete (originalWindow as unknown as { __VIBEGAME__?: unknown })
        .__VIBEGAME__;
    }
  });

  function fakeWindow(): Window & { __VIBEGAME__?: Record<string, unknown> } {
    const w = {} as Window & { __VIBEGAME__?: Record<string, unknown> };
    (globalThis as Record<string, unknown>).window = w;
    return w;
  }

  it('merges the debug namespace when profiler created __VIBEGAME__ first', () => {
    const w = fakeWindow();
    w.__VIBEGAME__ = { profiler: { top: () => 1 } };

    DebugPlugin.initialize!(new State());

    const debug = w.__VIBEGAME__?.debug as
      | {
          varNames: () => string[];
        }
      | undefined;
    expect(debug).toBeDefined();
    expect(w.__VIBEGAME__?.profiler).toBeDefined(); // preserved
    expect(debug?.varNames?.()).toEqual([]);
  });

  it('installs the full bridge when __VIBEGAME__ did not exist', () => {
    const w = fakeWindow();

    DebugPlugin.initialize!(new State());

    const bridge = w.__VIBEGAME__ as Record<string, unknown>;
    expect(bridge.debug).toBeDefined();
    expect(typeof bridge.entities).toBe('function');
    expect(typeof bridge.snapshot).toBe('function');
  });

  it('does not clobber an existing debug namespace', () => {
    const w = fakeWindow();
    const sentinel = { varNames: mock(() => ['existing']) };
    w.__VIBEGAME__ = { debug: sentinel };

    DebugPlugin.initialize!(new State());

    expect(w.__VIBEGAME__?.debug).toBe(sentinel);
  });
});
