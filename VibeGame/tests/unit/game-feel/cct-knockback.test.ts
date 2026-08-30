import { describe, expect, it } from 'bun:test';
import type { State } from 'vibegame';
import { Transform } from 'vibegame';
import {
  applyCctKnockback,
  clearCctKnockbacks,
  isCctKnockbackActive,
  tickCctKnockbacks,
} from 'vibegame';

const EID = 7;

interface StubState {
  exists: (eid: number) => boolean;
  hasComponent: (eid: number, component: unknown) => boolean;
}

function makeState(exists = true): State {
  const stub: StubState = {
    exists: () => exists,
    hasComponent: () => false,
  };
  return stub as unknown as State;
}

function place(eid: number, x: number, z: number): void {
  Transform.posX[eid] = x;
  Transform.posZ[eid] = z;
}

describe('applyCctKnockback — impact pushback', () => {
  it('rejects degenerate directions and ranges', () => {
    const state = makeState();
    place(EID, 0, 0);
    expect(applyCctKnockback(state, EID, 0, 0, 0.8, 0.2)).toBe(false);
    expect(applyCctKnockback(state, EID, 1, 0, 0, 0.2)).toBe(false);
    expect(applyCctKnockback(state, EID, 1, 0, 0.8, 0)).toBe(false);
    expect(isCctKnockbackActive(state, EID)).toBe(false);
  });

  it('travels the requested distance with ease-out and stops there', () => {
    const state = makeState();
    place(EID, 10, 0);
    expect(applyCctKnockback(state, EID, 1, 0, 0.8, 0.2)).toBe(true);
    expect(isCctKnockbackActive(state, EID)).toBe(true);

    // Halfway through (0.1s of 0.2s): ease-out quad covers 75% of the range.
    tickCctKnockbacks(state, 0.1);
    expect(Transform.posX[EID]).toBeCloseTo(10.6, 5);
    expect(isCctKnockbackActive(state, EID)).toBe(true);

    // Way past the end: clamped at origin + 0.8m, knocked back inactive.
    tickCctKnockbacks(state, 1.0);
    expect(Transform.posX[EID]).toBeCloseTo(10.8, 5);
    expect(isCctKnockbackActive(state, EID)).toBe(false);
    // A further tick must not move it (registry entry is gone).
    tickCctKnockbacks(state, 0.016);
    expect(Transform.posX[EID]).toBeCloseTo(10.8, 5);
  });

  it('normalizes the direction vector', () => {
    const state = makeState();
    place(EID, 0, 0);
    applyCctKnockback(state, EID, 5, 0, 1, 0.1);
    tickCctKnockbacks(state, 10);
    // Unit +X shove of 1m — magnitude of the input must not scale it.
    expect(Transform.posX[EID]).toBeCloseTo(1, 5);
    expect(Transform.posZ[EID]).toBeCloseTo(0, 5);
  });

  it('diagonal shoves land on the circle, not the square', () => {
    const state = makeState();
    place(EID, 0, 0);
    applyCctKnockback(state, EID, 1, 1, 1, 0.1);
    tickCctKnockbacks(state, 10);
    expect(Math.hypot(Transform.posX[EID], Transform.posZ[EID])).toBeCloseTo(
      1,
      5
    );
  });

  it('re-applying mid-flight re-bases the origin (fresh shove wins)', () => {
    const state = makeState();
    place(EID, 0, 0);
    applyCctKnockback(state, EID, 1, 0, 0.8, 0.2);
    tickCctKnockbacks(state, 0.1); // at 0.6m
    applyCctKnockback(state, EID, 1, 0, 0.8, 0.2); // new shove from here
    tickCctKnockbacks(state, 10);
    expect(Transform.posX[EID]).toBeCloseTo(0.6 + 0.8, 5);
  });

  it('drops entries for destroyed entities without touching them', () => {
    const state = makeState(false);
    place(EID, 0, 0);
    applyCctKnockback(state, EID, 1, 0, 1, 0.1);
    const xBefore = Transform.posX[EID];
    tickCctKnockbacks(state, 0.016);
    expect(Transform.posX[EID]).toBe(xBefore);
    expect(isCctKnockbackActive(state, EID)).toBe(false);
  });

  it('clearCctKnockbacks empties the registry', () => {
    const state = makeState();
    place(EID, 0, 0);
    applyCctKnockback(state, EID, 1, 0, 1, 0.1);
    clearCctKnockbacks(state);
    expect(isCctKnockbackActive(state, EID)).toBe(false);
  });
});
