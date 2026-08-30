import { describe, expect, it } from 'bun:test';
import type { State } from 'vibegame';
import { hitStop, hitStopActive, tickHitStop } from 'vibegame';

function makeState(timeScale = 1): State {
  return {
    time: { timeScale, unscaledDeltaTime: 0.016 },
  } as unknown as State;
}

describe('hitStop — impact freeze frames', () => {
  it('freezes the world at the requested time scale', () => {
    const state = makeState(1);
    hitStop(state, 0.07, 0.05);
    expect(state.time.timeScale).toBe(0.05);
    expect(hitStopActive(state)).toBe(true);
  });

  it('restores the previous time scale once the window expires (unscaled dt)', () => {
    const state = makeState(1);
    hitStop(state, 0.07, 0.05);
    // Halfway: still frozen.
    tickHitStop(state, 0.03);
    expect(state.time.timeScale).toBe(0.05);
    expect(hitStopActive(state)).toBe(true);
    // Past the window: restored.
    tickHitStop(state, 0.05);
    expect(state.time.timeScale).toBe(1);
    expect(hitStopActive(state)).toBe(false);
  });

  it('restores a non-1 baseline (stacking with other slow-mo)', () => {
    const state = makeState(0.5);
    hitStop(state, 0.05, 0.05);
    tickHitStop(state, 0.06);
    expect(state.time.timeScale).toBe(0.5);
  });

  it('re-applies the freeze when another system resets the scale (pause coordinator pattern)', () => {
    const state = makeState(1);
    hitStop(state, 0.1, 0.05);
    // The pause system re-asserts its contract mid-stop and wipes the freeze.
    state.time.timeScale = 1;
    tickHitStop(state, 0.016);
    expect(state.time.timeScale).toBe(0.05);
    expect(hitStopActive(state)).toBe(true);
    // Still expires on schedule after the re-assert.
    tickHitStop(state, 0.1);
    expect(state.time.timeScale).toBe(1);
  });

  it('holds (does not tick or fight) while hard-paused at timeScale 0', () => {
    const state = makeState(1);
    hitStop(state, 0.1, 0.05);
    state.time.timeScale = 0; // modal pause wins
    tickHitStop(state, 5.0);
    expect(state.time.timeScale).toBe(0);
    expect(hitStopActive(state)).toBe(true); // pending until unpause
  });

  it('overlapping stops keep the strongest freeze and longest window', () => {
    const state = makeState(1);
    hitStop(state, 0.05, 0.1);
    hitStop(state, 0.2, 0.02);
    expect(state.time.timeScale).toBe(0.02);
    // 0.05s of unscaled time is not enough for the extended window.
    tickHitStop(state, 0.06);
    expect(hitStopActive(state)).toBe(true);
    tickHitStop(state, 0.15);
    expect(hitStopActive(state)).toBe(false);
    expect(state.time.timeScale).toBe(1);
  });

  it('ignores degenerate requests', () => {
    const state = makeState(1);
    hitStop(state, 0, 0.05);
    hitStop(state, 0.1, 1);
    expect(state.time.timeScale).toBe(1);
    expect(hitStopActive(state)).toBe(false);
  });

  it('keeps states isolated (no cross-contamination)', () => {
    const a = makeState(1);
    const b = makeState(1);
    hitStop(a, 0.1, 0.05);
    expect(b.time.timeScale).toBe(1);
    expect(hitStopActive(b)).toBe(false);
  });
});
