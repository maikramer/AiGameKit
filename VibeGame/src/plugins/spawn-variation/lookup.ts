import type { State } from '../../core';
import { Parent } from '../../core';
import { SpawnVariation } from './components';

export interface SpawnVariationValues {
  colorR: number;
  colorG: number;
  colorB: number;
  brightness: number;
  contrast: number;
}

/**
 * Read SpawnVariation from the entity or an ancestor (GameObject root →
 * GLTFLoader child used by the instanced pool).
 */
export function findSpawnVariation(
  state: State,
  eid: number
): SpawnVariationValues | null {
  let cur = eid;
  for (let i = 0; i < 8; i++) {
    if (state.hasComponent(cur, SpawnVariation)) {
      return {
        colorR: SpawnVariation.colorR[cur],
        colorG: SpawnVariation.colorG[cur],
        colorB: SpawnVariation.colorB[cur],
        brightness: SpawnVariation.brightness[cur],
        contrast: SpawnVariation.contrast[cur],
      };
    }
    if (!state.hasComponent(cur, Parent)) break;
    const p = Parent.entity[cur];
    if (p <= 0 || p === cur) break;
    cur = p;
  }
  return null;
}
