import type { State } from '../../core';

/** Handles yuka (tipagem relaxada — pacote sem `.d.ts`). */
export interface SteeringRow {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- yuka sem tipos
  vehicle: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  seek?: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  flee?: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  wander?: any;
}

const stateToSteering = new WeakMap<State, Map<number, SteeringRow>>();

export function getSteeringMap(state: State): Map<number, SteeringRow> {
  let m = stateToSteering.get(state);
  if (!m) {
    m = new Map();
    stateToSteering.set(state, m);
  }
  return m;
}
