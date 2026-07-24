import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  isSceneShadersWarmed,
  resetShaderWarmup,
  warmupSceneShaders,
} from 'vibegame';

describe('shader-warmup', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
    resetShaderWarmup(state);
  });

  it('headless warmup latches immediately', () => {
    expect(isSceneShadersWarmed(state)).toBe(false);
    expect(warmupSceneShaders(state)).toBe(true);
    expect(isSceneShadersWarmed(state)).toBe(true);
    expect(warmupSceneShaders(state)).toBe(true);
  });

  it('resetShaderWarmup clears the latch', () => {
    warmupSceneShaders(state);
    resetShaderWarmup(state);
    expect(isSceneShadersWarmed(state)).toBe(false);
  });
});
