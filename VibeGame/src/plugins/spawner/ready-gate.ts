import {
  defineQuery,
  registerReadyGate,
  type State,
  type System,
} from '../../core';
import { PlacePending, SpawnerPending } from './components';

const spawnerQuery = defineQuery([SpawnerPending]);
const placeQuery = defineQuery([PlacePending]);

/**
 * Spawn readiness for the loading gate: every spawn/place group has been
 * processed. Spawned entities load their GLB visuals asynchronously through the
 * shared instance pool (gltf-xml/auto-instance), which streams in after the gate
 * the same way individual GLTF loads do.
 */
function spawnReady(state: State): boolean {
  for (const eid of spawnerQuery(state.world)) {
    if (!SpawnerPending.spawned[eid]) return false;
  }
  for (const eid of placeQuery(state.world)) {
    if (!PlacePending.spawned[eid]) return false;
  }
  return true;
}

export const SpawnReadyGateSystem: System = {
  group: 'setup',
  setup(state) {
    registerReadyGate(state, 'spawn', spawnReady);
  },
};
