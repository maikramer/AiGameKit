import type { State } from '../../core';
import { Transform } from '../transforms/components';
import { TerrainSpawned } from './components';
import { sampleTerrainSurface } from './surface';

/**
 * World Y for a `TerrainSpawned` entity at its current XZ.
 *
 * Contract (statics / place only):
 * - `yOffset` = foot plant (`baseYOffset` + optional AABB lift).
 * Creatures skip `TerrainSpawned`; CCT / heightfield owns runtime Y.
 */
export function terrainSpawnedWorldY(
  state: State,
  eid: number,
  wx: number,
  wz: number
): number | null {
  const eps = TerrainSpawned.surfaceEpsilon[eid] || 0.75;
  const s = sampleTerrainSurface(state, wx, wz, eps);
  if (!s) return null;
  return s.worldY + TerrainSpawned.yOffset[eid];
}

/** Write `Transform.posY` from {@link terrainSpawnedWorldY}. */
export function applyTerrainSpawnedY(state: State, eid: number): boolean {
  if (!state.hasComponent(eid, TerrainSpawned)) return false;
  const y = terrainSpawnedWorldY(
    state,
    eid,
    Transform.posX[eid],
    Transform.posZ[eid]
  );
  if (y === null) return false;
  Transform.posY[eid] = y;
  Transform.dirty[eid] = 1;
  return true;
}
