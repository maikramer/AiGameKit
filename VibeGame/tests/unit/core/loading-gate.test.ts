import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  getLoadingProgress,
  isPhysicsHeld,
  isWorldLoadedLatched,
  isWorldReady,
  registerReadyGate,
  setLoadingEnforcement,
} from 'vibegame';

describe('loading-gate registry', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
  });

  it('is vacuously ready with no gates', () => {
    expect(isWorldReady(state)).toBe(true);
    const p = getLoadingProgress(state);
    expect(p.total).toBe(0);
    expect(p.pending).toEqual([]);
  });

  it('aggregates gates and reports pending ones', () => {
    let terrainOk = false;
    registerReadyGate(state, 'terrain', () => terrainOk);
    registerReadyGate(state, 'assets', () => true);

    expect(isWorldReady(state)).toBe(false);
    expect(getLoadingProgress(state)).toMatchObject({
      ready: 1,
      total: 2,
      pending: ['terrain'],
    });

    terrainOk = true;
    expect(isWorldReady(state)).toBe(true);
    expect(getLoadingProgress(state).pending).toEqual([]);
  });

  it('registration is idempotent by name', () => {
    registerReadyGate(state, 'terrain', () => false);
    registerReadyGate(state, 'terrain', () => true);
    expect(getLoadingProgress(state).total).toBe(1);
    expect(isWorldReady(state)).toBe(true);
  });

  it('physics is never held without enforcement', () => {
    registerReadyGate(state, 'terrain', () => false);
    expect(isPhysicsHeld(state)).toBe(false);
  });

  it('physics is held under enforcement until ready, then latches', () => {
    let ready = false;
    registerReadyGate(state, 'terrain', () => ready);
    setLoadingEnforcement(state, true);

    expect(isPhysicsHeld(state)).toBe(true);
    expect(isWorldLoadedLatched(state)).toBe(false);

    ready = true;
    // First ready observation latches.
    expect(isPhysicsHeld(state)).toBe(false);
    expect(isWorldLoadedLatched(state)).toBe(true);

    // Transient un-readiness during gameplay must NOT re-engage the hold.
    ready = false;
    expect(isPhysicsHeld(state)).toBe(false);
    expect(isWorldLoadedLatched(state)).toBe(true);
  });
});
