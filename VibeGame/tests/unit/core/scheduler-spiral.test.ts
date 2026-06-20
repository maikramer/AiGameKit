import { beforeEach, describe, expect, it } from 'bun:test';
import type { System } from 'vibegame';
import { State, TIME_CONSTANTS } from 'vibegame';

describe('Scheduler spiral-of-death cap (A3)', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
  });

  it('caps fixed-step iterations at MAX_FIXED_STEPS_PER_FRAME on a huge delta', () => {
    let fixedRuns = 0;
    const counterSystem: System = {
      group: 'fixed',
      update: () => {
        fixedRuns++;
      },
    };
    state.registerSystem(counterSystem);

    state.step(10);

    expect(fixedRuns).toBeLessThanOrEqual(
      TIME_CONSTANTS.MAX_FIXED_STEPS_PER_FRAME
    );
    expect(fixedRuns).toBe(TIME_CONSTANTS.MAX_FIXED_STEPS_PER_FRAME);
  });

  it('advances fixedTime by at most maxSteps * FIXED_TIMESTEP', () => {
    const fixedT0 = state.time.fixedTime;
    const counterSystem: System = {
      group: 'fixed',
      update: () => {},
    };
    state.registerSystem(counterSystem);

    state.step(10);

    const maxAdvance =
      TIME_CONSTANTS.MAX_FIXED_STEPS_PER_FRAME * TIME_CONSTANTS.FIXED_TIMESTEP;
    expect(state.time.fixedTime - fixedT0).toBeLessThanOrEqual(
      maxAdvance + 1e-9
    );
  });

  it('scheduler.setMaxFixedStepsPerFrame lowers the cap', () => {
    let fixedRuns = 0;
    const counterSystem: System = {
      group: 'fixed',
      update: () => {
        fixedRuns++;
      },
    };
    state.registerSystem(counterSystem);

    state.scheduler.setMaxFixedStepsPerFrame(5);

    state.step(10);

    expect(fixedRuns).toBe(5);
    expect(state.scheduler.getMaxFixedStepsPerFrame()).toBe(5);
  });

  it('does not cap when the delta fits within the iteration budget', () => {
    let fixedRuns = 0;
    const counterSystem: System = {
      group: 'fixed',
      update: () => {
        fixedRuns++;
      },
    };
    state.registerSystem(counterSystem);

    const delta = TIME_CONSTANTS.FIXED_TIMESTEP * 3;
    state.step(delta);

    expect(fixedRuns).toBeGreaterThan(0);
    expect(fixedRuns).toBeLessThan(TIME_CONSTANTS.MAX_FIXED_STEPS_PER_FRAME);
  });
});
