import { describe, expect, it } from 'bun:test';
import { createSnapshot, restoreSnapshot, State } from 'vibegame';

describe('snapshot time restore', () => {
  it('keeps elapsed coherent with realtime after the next step', () => {
    const state = new State();
    state.headless = true;

    state.time.realtimeSinceStartup = 10;
    state.time.elapsed = 10;
    state.time.fixedTime = 2;

    const snap = createSnapshot(state);
    expect(snap.realtimeSinceStartup).toBeCloseTo(10);
    expect(snap.fixedTime).toBeCloseTo(2);

    const restored = new State();
    restored.headless = true;
    restoreSnapshot(restored, snap);

    expect(restored.time.realtimeSinceStartup).toBeCloseTo(10);
    expect(restored.time.elapsed).toBeCloseTo(10);
    expect(restored.time.fixedTime).toBeCloseTo(2);

    restored.step(0.016);
    expect(restored.time.elapsed).toBeCloseTo(10.016, 5);
    expect(restored.time.realtimeSinceStartup).toBeCloseTo(10.016, 5);
  });

  it('falls back to elapsed when realtimeSinceStartup is missing (legacy)', () => {
    const state = new State();
    state.headless = true;
    restoreSnapshot(state, {
      elapsed: 7.5,
      entities: [],
    });
    expect(state.time.realtimeSinceStartup).toBeCloseTo(7.5);
    expect(state.time.elapsed).toBeCloseTo(7.5);
  });
});
