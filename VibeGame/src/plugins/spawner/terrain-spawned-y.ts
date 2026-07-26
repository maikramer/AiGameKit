import type { State } from '../../core';
import { Transform } from '../transforms/components';
import { TerrainSpawned } from './components';
import {
  sampleTerrainSurface,
  sinkOffsetForSlope,
  slopeAngleRad,
} from './surface';

/**
 * World Y for a `TerrainSpawned` entity at its current XZ.
 *
 * Contract (statics / place only — not DynamicSpawner agents):
 * - `yOffset` = foot plant (`baseYOffset` + optional AABB lift). Never edge-sink.
 * - Edge-sink recomputed when `halfWidth > 0`.
 *
 * Dynamic enemies do **not** use this path: no Rigidbody/CCT → Rapier never
 * grounds them; attaching TerrainSpawned + per-frame snap was a fake ground.
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

  const halfWidth = TerrainSpawned.halfWidth[eid] || 0;
  let sink = 0;
  if (halfWidth > 0) {
    const slope = slopeAngleRad(s.normal);
    const aligned = TerrainSpawned.alignToTerrain[eid] === 1;
    sink = sinkOffsetForSlope(slope, halfWidth, aligned ? slope : 0);
  }

  return s.worldY + TerrainSpawned.yOffset[eid] - sink;
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
