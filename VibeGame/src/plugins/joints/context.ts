import type { ImpulseJoint } from '@dimforge/rapier3d-compat';
import type { State } from '../../core';

const stateToJoints = new WeakMap<State, Map<number, ImpulseJoint>>();

export function getJointHandles(state: State): Map<number, ImpulseJoint> {
  let m = stateToJoints.get(state);
  if (!m) {
    m = new Map();
    stateToJoints.set(state, m);
  }
  return m;
}
