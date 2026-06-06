import { defineQuery, registerReadyGate, type State, type System } from '../../core';
import { Terrain } from './components';
import { getTerrainContext, isTerrainDynamicsBlocking } from './utils';

const terrainQuery = defineQuery([Terrain]);

/**
 * Terrain readiness for the loading gate: a declared terrain field is fully
 * loaded when its heightmap has decoded and its collision is ready. Scenes with
 * no terrain report ready (nothing to wait for).
 */
function terrainReady(state: State): boolean {
  if (terrainQuery(state.world).length === 0) return true;

  let anyInitialized = false;
  for (const [, data] of getTerrainContext(state)) {
    if (!data.initialized) continue;
    // A heightmapped field is `initialized` with a flat sampler before the
    // image decodes — wait for the real heights.
    if (data.heightmapUrl && !data.sampler.data) return false;
    anyInitialized = true;
  }
  if (!anyInitialized) return false;

  return !isTerrainDynamicsBlocking(state);
}

export const TerrainReadyGateSystem: System = {
  group: 'setup',
  setup(state) {
    registerReadyGate(state, 'terrain', terrainReady);
  },
};
