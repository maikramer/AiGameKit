import { defineQuery } from 'bitecs';
import type { State } from '../../core';
import { Water } from '../water/components';
import { WorldTransform } from '../transforms/components';

const waterWithTransformQuery = defineQuery([Water, WorldTransform]);

/** Margin above water surface Y (world): terrain below this is treated as wet / underwater. */
const SURFACE_CLEARANCE = 0.45;

/**
 * True if (wx, wz) lies under a water plane's XZ extent and terrain is at or below the surface.
 * Used to skip spawning props (trees, etc.) on lake beds or under misaligned water sheets.
 */
export function isTerrainUnderwaterAt(
  state: State,
  wx: number,
  wz: number,
  terrainY: number
): boolean {
  for (const eid of waterWithTransformQuery(state.world)) {
    const ox = WorldTransform.posX[eid];
    const oy = WorldTransform.posY[eid];
    const oz = WorldTransform.posZ[eid];
    const half = Water.size[eid] * 0.5;
    if (wx < ox - half || wx > ox + half || wz < oz - half || wz > oz + half) {
      continue;
    }
    if (terrainY < oy + SURFACE_CLEARANCE) {
      return true;
    }
  }
  return false;
}
