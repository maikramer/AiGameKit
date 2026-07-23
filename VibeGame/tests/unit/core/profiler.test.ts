import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import type { System } from 'vibegame';
import {
  State,
  _resetProfilerForTests,
  disableProfiler,
  enableProfiler,
  getProfilerMode,
  getProfilerSnapshot,
  getProfilerTop,
  isProfilerEnabled,
  resetProfiler,
  withSpan,
} from 'vibegame';

describe('ProfilerSession', () => {
  beforeEach(() => {
    _resetProfilerForTests();
  });

  afterEach(() => {
    _resetProfilerForTests();
  });

  it('stays off by default with zero cost path', () => {
    expect(isProfilerEnabled()).toBe(false);
    expect(getProfilerMode()).toBe('off');
    const state = new State();
    state.headless = true;
    state.registerSystem({
      name: 'noop',
      update() {},
    });
    state.step(1 / 60);
    expect(getProfilerSnapshot().systems).toEqual([]);
  });

  it('enable/disable toggles the gate', () => {
    enableProfiler('sample');
    expect(isProfilerEnabled()).toBe(true);
    expect(getProfilerMode()).toBe('sample');
    disableProfiler();
    expect(isProfilerEnabled()).toBe(false);
    expect(getProfilerMode()).toBe('off');
  });

  it('ranks the slower system first in top()', () => {
    enableProfiler('sample');
    const state = new State();
    state.headless = true;

    const fast: System = {
      name: 'FastSystem',
      group: 'simulation',
      update() {
        // intentional no-op
      },
    };
    const slow: System = {
      name: 'SlowSystem',
      group: 'simulation',
      update() {
        const end = performance.now() + 2;
        while (performance.now() < end) {
          // busy-wait ~2ms
        }
      },
    };
    state.registerSystem(fast);
    state.registerSystem(slow);

    for (let i = 0; i < 8; i++) {
      state.step(1 / 60);
    }

    const top = getProfilerTop(5);
    expect(top.length).toBeGreaterThan(0);
    expect(top[0].name).toBe('SlowSystem');
    expect(top[0].avgMs).toBeGreaterThan(1);

    const snap = getProfilerSnapshot();
    expect(snap.mode).toBe('sample');
    expect(snap.windowFrames).toBeGreaterThan(0);
    expect(snap.systems.some((s) => s.name === 'FastSystem')).toBe(true);
    expect(
      snap.groups.some((g) => g.group === 'simulation' && g.avgMs > 0)
    ).toBe(true);
  });

  it('records custom spans via withSpan', () => {
    enableProfiler('sample');
    const state = new State();
    state.headless = true;
    state.registerSystem({
      name: 'SpanHost',
      update() {
        withSpan('game/work', () => {
          const end = performance.now() + 1;
          while (performance.now() < end) {
            // busy-wait
          }
        });
      },
    });

    for (let i = 0; i < 5; i++) state.step(1 / 60);

    const snap = getProfilerSnapshot();
    const custom = snap.customs.find((c) => c.name === 'game/work');
    expect(custom).toBeDefined();
    expect(custom!.avgMs).toBeGreaterThan(0.5);
  });

  it('resetProfiler clears rings but keeps mode', () => {
    enableProfiler('sample');
    const state = new State();
    state.headless = true;
    state.registerSystem({
      name: 'A',
      update() {},
    });
    state.step(1 / 60);
    expect(getProfilerSnapshot().systems.length).toBeGreaterThan(0);

    resetProfiler();
    expect(isProfilerEnabled()).toBe(true);
    expect(getProfilerSnapshot().systems).toEqual([]);
    expect(getProfilerSnapshot().windowFrames).toBe(0);
  });

  it('snapshot shape is stable', () => {
    enableProfiler('deep');
    const state = new State();
    state.headless = true;
    state.registerSystem({
      name: 'Named',
      group: 'late',
      update() {},
    });
    state.step(1 / 60);
    const snap = getProfilerSnapshot();
    expect(snap).toMatchObject({
      mode: 'deep',
      frozen: false,
    });
    expect(typeof snap.fps).toBe('number');
    expect(typeof snap.frameAvgMs).toBe('number');
    expect(Array.isArray(snap.groups)).toBe(true);
    expect(Array.isArray(snap.systems)).toBe(true);
    expect(Array.isArray(snap.customs)).toBe(true);
    expect(typeof snap.timestamp).toBe('number');
  });
});
