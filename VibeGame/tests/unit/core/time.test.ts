import { beforeEach, describe, expect, it } from 'bun:test';
import { State, Time } from 'vibegame';

describe('Time', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    Time.init(state);
  });

  it('should default timeScale to 1.0', () => {
    expect(state.time.timeScale).toBe(1.0);
    expect(Time.timeScale).toBe(1.0);
  });

  it('should default frameCount to 0', () => {
    expect(state.time.frameCount).toBe(0);
    expect(Time.frameCount).toBe(0);
  });

  it('should increment frameCount each step', () => {
    expect(state.time.frameCount).toBe(0);
    state.step();
    expect(state.time.frameCount).toBe(1);
    state.step();
    expect(state.time.frameCount).toBe(2);
    expect(Time.frameCount).toBe(2);
  });

  it('should compute deltaTime as unscaledDeltaTime * timeScale', () => {
    state.step(0.016);
    expect(state.time.unscaledDeltaTime).toBeCloseTo(0.016, 6);
    expect(state.time.deltaTime).toBeCloseTo(0.016, 6);

    state.time.timeScale = 0.5;
    state.step(0.016);
    expect(state.time.unscaledDeltaTime).toBeCloseTo(0.016, 6);
    expect(state.time.deltaTime).toBeCloseTo(0.008, 6);
  });

  it('should not affect unscaledDeltaTime when timeScale changes', () => {
    state.time.timeScale = 2.0;
    state.step(0.016);
    expect(state.time.unscaledDeltaTime).toBeCloseTo(0.016, 6);
    expect(state.time.deltaTime).toBeCloseTo(0.032, 6);
  });

  it('should set timeScale via Time utility', () => {
    Time.timeScale = 0.5;
    expect(state.time.timeScale).toBe(0.5);
    expect(Time.timeScale).toBe(0.5);
  });

  it('should track realtimeSinceStartup', () => {
    expect(state.time.realtimeSinceStartup).toBe(0);
    state.step(0.016);
    expect(state.time.realtimeSinceStartup).toBeCloseTo(0.016, 6);
    state.step(0.016);
    expect(state.time.realtimeSinceStartup).toBeCloseTo(0.032, 6);
  });

  it('should track realtimeSinceStartup unaffected by timeScale', () => {
    state.time.timeScale = 0.5;
    state.step(0.016);
    expect(state.time.realtimeSinceStartup).toBeCloseTo(0.016, 6);
    state.step(0.016);
    expect(state.time.realtimeSinceStartup).toBeCloseTo(0.032, 6);
  });

  it('should have correct fixedDeltaTime default', () => {
    expect(state.time.fixedDeltaTime).toBeCloseTo(1 / 50, 6);
    expect(Time.fixedDeltaTime).toBeCloseTo(1 / 50, 6);
  });

  it('should track fixedTime', () => {
    expect(state.time.fixedTime).toBe(0);
  });

  it('should keep elapsed as alias for realtimeSinceStartup', () => {
    state.step(0.016);
    expect(state.time.elapsed).toBeCloseTo(state.time.realtimeSinceStartup, 6);
  });

  it('should expose all properties via Time utility getters', () => {
    state.step(0.016);
    expect(Time.deltaTime).toBe(state.time.deltaTime);
    expect(Time.unscaledDeltaTime).toBe(state.time.unscaledDeltaTime);
    expect(Time.frameCount).toBe(state.time.frameCount);
    expect(Time.fixedTime).toBe(state.time.fixedTime);
    expect(Time.fixedDeltaTime).toBe(state.time.fixedDeltaTime);
    expect(Time.realtimeSinceStartup).toBe(state.time.realtimeSinceStartup);
    expect(Time.time).toBe(state.time.realtimeSinceStartup);
  });

  it('should set timeScale to zero and freeze deltaTime', () => {
    state.time.timeScale = 0;
    state.step(0.016);
    expect(state.time.deltaTime).toBe(0);
    expect(state.time.unscaledDeltaTime).toBeCloseTo(0.016, 6);
  });
});
