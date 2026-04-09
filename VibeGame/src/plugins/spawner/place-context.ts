import type { State } from '../../core';
import type { PlacementSpec } from './place-types';

const stateToPlacement = new WeakMap<State, Map<number, PlacementSpec>>();

export function getPlacementSpecs(state: State): Map<number, PlacementSpec> {
  let m = stateToPlacement.get(state);
  if (!m) {
    m = new Map();
    stateToPlacement.set(state, m);
  }
  return m;
}

export function setPlacementSpec(
  state: State,
  entity: number,
  spec: PlacementSpec
): void {
  getPlacementSpecs(state).set(entity, spec);
}
