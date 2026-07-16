import type { State } from '../../core';
import { isSpawnAreaFree } from '../spawner/occupancy';
import { isPointNearWater, isPointOnWaterBank } from '../water/registry';

export type HubXZ = [number, number];

const hubsByPatch = new WeakMap<State, Map<number, HubXZ[]>>();

function mulberry32(seed: number): () => number {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

export function getVegetationHubs(
  state: State,
  patchEntity: number
): HubXZ[] | undefined {
  return hubsByPatch.get(state)?.get(patchEntity);
}

export function setVegetationHubs(
  state: State,
  patchEntity: number,
  hubs: HubXZ[]
): void {
  let m = hubsByPatch.get(state);
  if (!m) {
    m = new Map();
    hubsByPatch.set(state, m);
  }
  m.set(patchEntity, hubs);
}

export function clearVegetationHubs(state: State, patchEntity?: number): void {
  if (patchEntity === undefined) {
    hubsByPatch.delete(state);
    return;
  }
  hubsByPatch.get(state)?.delete(patchEntity);
}

/** Test helper. */
export function _resetVegetationHubs(state: State): void {
  hubsByPatch.delete(state);
}

export interface GenerateHubsOptions {
  seed: number;
  clusterCount: number;
  regionMinX: number;
  regionMaxX: number;
  regionMinZ: number;
  regionMaxZ: number;
  avoidWater: boolean;
  nearWater?: boolean;
}

/**
 * Sample cluster hubs in a region (same rules as TerrainSpawnSystem hub loop).
 */
export function generateVegetationHubs(
  state: State,
  opts: GenerateHubsOptions
): HubXZ[] {
  const hubs: HubXZ[] = [];
  if (opts.clusterCount <= 0) return hubs;
  const rand = mulberry32(opts.seed >>> 0);
  const minX = opts.regionMinX;
  const maxX = opts.regionMaxX;
  const minZ = opts.regionMinZ;
  const maxZ = opts.regionMaxZ;
  const cAttempts = Math.max(opts.clusterCount * 8, 32);
  for (let c = 0; c < cAttempts && hubs.length < opts.clusterCount; c++) {
    const cx0 = minX + rand() * (maxX - minX);
    const cz0 = minZ + rand() * (maxZ - minZ);
    if (!isSpawnAreaFree(state, cx0, cz0, 0.5)) continue;
    if (opts.avoidWater && isPointNearWater(state, cx0, cz0)) continue;
    if (opts.nearWater && !isPointOnWaterBank(state, cx0, cz0)) continue;
    hubs.push([cx0, cz0]);
  }
  return hubs;
}
