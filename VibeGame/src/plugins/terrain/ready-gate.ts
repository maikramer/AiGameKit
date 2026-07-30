import {
  defineSystem,
  defineQuery,
  registerReadyGate,
  type State,
  type System,
} from '../../core';
import { Terrain } from './components';
import { getTerrainContext, isTerrainDynamicsBlocking } from './utils';

const terrainQuery = defineQuery([Terrain]);

/**
 * Reports whether every declared terrain field is usable: heightmap decoded
 * (when one is set) and Rapier heightfield collision built. Scenes with no
 * terrain report ready. This is both the loading-gate signal and the public
 * API games poll to defer spawn-time ground snapping until the floor exists.
 */
export function terrainReady(state: State): boolean {
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

/** Snapshot for the loading overlay while the `terrain` gate is held. */
export function describeTerrainPending(state: State): {
  phase: 'none' | 'init' | 'decode' | 'collision' | 'ready';
  fields: number;
} {
  const fields = terrainQuery(state.world).length;
  if (fields === 0) return { phase: 'none', fields: 0 };

  let anyInitialized = false;
  let waitingDecode = false;
  for (const [, data] of getTerrainContext(state)) {
    if (!data.initialized) continue;
    anyInitialized = true;
    if (data.heightmapUrl && !data.sampler.data) waitingDecode = true;
  }
  if (!anyInitialized) return { phase: 'init', fields };
  if (waitingDecode) return { phase: 'decode', fields };
  if (isTerrainDynamicsBlocking(state)) return { phase: 'collision', fields };
  return { phase: 'ready', fields };
}

export const TerrainReadyGateSystem: System = defineSystem({
  name: 'TerrainReadyGateSystem',
  group: 'setup',
  setup(state) {
    registerReadyGate(state, 'terrain', terrainReady);
  },
});
