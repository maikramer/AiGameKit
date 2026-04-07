import type { State } from '../../core';
import type { SpawnGroupSpec } from './types';

const stateToSpecs = new WeakMap<State, Map<number, SpawnGroupSpec>>();

export function getSpawnGroupSpecs(state: State): Map<number, SpawnGroupSpec> {
  let m = stateToSpecs.get(state);
  if (!m) {
    m = new Map();
    stateToSpecs.set(state, m);
  }
  return m;
}

export function setSpawnGroupSpec(
  state: State,
  entity: number,
  spec: SpawnGroupSpec
): void {
  getSpawnGroupSpecs(state).set(entity, spec);
}
