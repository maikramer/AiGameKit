import type { State } from '../../core';
import type { SteeringVehicle } from './vehicle';

export interface SteeringRow {
  vehicle: SteeringVehicle;
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
