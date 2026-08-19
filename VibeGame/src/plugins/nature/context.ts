import type { State } from '../../core';
import type { NatureRulesPlan } from './rules';

export interface NatureRuntime {
  plan: NatureRulesPlan;
  /** Set once the planner emitted the per-species SpawnGroupSpecs. */
  planned: boolean;
}

const PLANS = new WeakMap<State, Map<number, NatureRuntime>>();

export function getNaturePlans(state: State): Map<number, NatureRuntime> {
  let m = PLANS.get(state);
  if (!m) {
    m = new Map();
    PLANS.set(state, m);
  }
  return m;
}

export function setNaturePlan(
  state: State,
  entity: number,
  runtime: NatureRuntime
): void {
  getNaturePlans(state).set(entity, runtime);
}
