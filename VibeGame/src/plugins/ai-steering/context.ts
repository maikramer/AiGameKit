import type { FleeBehavior, SeekBehavior, Vehicle, WanderBehavior } from 'yuka';

import type { State } from '../../core';

export interface SteeringRow {
  vehicle: Vehicle;
  seek?: SeekBehavior;
  flee?: FleeBehavior;
  wander?: WanderBehavior;
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
