import {
  defineQuery,
  registerReadyGate,
  type State,
  type System,
} from '../../core';
import { PlacePending, SpawnerPending } from './components';
import { vegetationInstancerMap } from './systems';

const spawnerQuery = defineQuery([SpawnerPending]);
const placeQuery = defineQuery([PlacePending]);

/**
 * Spawn readiness for the loading gate: every spawn/place group has been
 * processed AND every instanced-vegetation group has finished loading its GLBs.
 * The spawn systems mark groups `spawned=1` synchronously but vegetation
 * instancing completes asynchronously, so `isReady()` is the real signal.
 */
function spawnReady(state: State): boolean {
  for (const eid of spawnerQuery(state.world)) {
    if (!SpawnerPending.spawned[eid]) return false;
  }
  for (const eid of placeQuery(state.world)) {
    if (!PlacePending.spawned[eid]) return false;
  }
  for (const [, instancer] of vegetationInstancerMap) {
    if (!instancer.isReady()) return false;
  }
  return true;
}

export const SpawnReadyGateSystem: System = {
  group: 'setup',
  setup(state) {
    registerReadyGate(state, 'spawn', spawnReady);
  },
};
