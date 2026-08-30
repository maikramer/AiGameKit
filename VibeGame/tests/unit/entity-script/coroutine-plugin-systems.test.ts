import { describe, expect, it } from 'bun:test';
import {
  EntityScriptPlugin,
  startCoroutine,
  State,
  WaitForEndOfFrame,
  WaitForFixedUpdate,
} from 'aigamekit-vibegame';

describe('EntityScriptPlugin coroutine systems', () => {
  it('advances WaitForEndOfFrame via registered late system', () => {
    const state = new State();
    state.registerPlugin(EntityScriptPlugin);
    const eid = state.createEntity();
    const log: string[] = [];

    function* gen() {
      log.push('start');
      yield WaitForEndOfFrame();
      log.push('after-eof');
    }

    startCoroutine(state, eid, gen);
    expect(log).toEqual(['start']);

    state.step();
    expect(log).toEqual(['start', 'after-eof']);
  });

  it('advances WaitForFixedUpdate via registered fixed system', () => {
    const state = new State();
    state.registerPlugin(EntityScriptPlugin);
    const eid = state.createEntity();
    const log: string[] = [];

    function* gen() {
      log.push('start');
      yield WaitForFixedUpdate();
      log.push('after-fixed');
    }

    startCoroutine(state, eid, gen);
    expect(log).toEqual(['start']);

    // FIXED_TIMESTEP = 0.02 — need accumulator >= one fixed step.
    state.step(0.05);
    expect(log).toEqual(['start', 'after-fixed']);
  });
});
