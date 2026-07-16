import type { State } from '../../core';
import type { VegetationPatchPlan } from './plan';

export interface VegetationPatchRuntime {
  plan: VegetationPatchPlan;
  /** Child entity ids that own layer SpawnGroupSpecs (smart mode). */
  layerEntities: number[];
  /** 1 once hubs were injected into layer specs. */
  hubsReady: boolean;
}

const patchesByState = new WeakMap<
  State,
  Map<number, VegetationPatchRuntime>
>();

export function getVegetationPatches(
  state: State
): Map<number, VegetationPatchRuntime> {
  let m = patchesByState.get(state);
  if (!m) {
    m = new Map();
    patchesByState.set(state, m);
  }
  return m;
}

export function setVegetationPatch(
  state: State,
  entity: number,
  runtime: VegetationPatchRuntime
): void {
  getVegetationPatches(state).set(entity, runtime);
}

export function getVegetationPatch(
  state: State,
  entity: number
): VegetationPatchRuntime | undefined {
  return getVegetationPatches(state).get(entity);
}

/** Test helper. */
export function _resetVegetationPatches(state: State): void {
  patchesByState.delete(state);
}
