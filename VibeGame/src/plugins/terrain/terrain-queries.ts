import { logger } from '../../core/utils/logger';
import type { State } from '../../core';
import { getRapierWorld } from '../physics';
import { Terrain, TerrainChunk, TerrainDebugInfo } from './components';
import { sampleHeightAt } from './height-sampler';
import { loadHeightfield } from './ahgt-loader';
import {
  getChunkMeshRegistry,
  getTerrainContext,
  setTerrainHeightmapUrl,
} from './utils';
import { applyLoadedSampler } from './systems';

export function getTerrainHeightAt(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (!data.initialized) continue;
    const localX = worldX - data.worldOffset.x;
    const localZ = worldZ - data.worldOffset.z;
    return sampleHeightAt(data.sampler, localX, localZ);
  }
  return 0;
}

/**
 * True if the terrain has a real chunk heightfield collider that covers
 * (worldX, worldZ). Unlike terrainReady(), this checks the actual collider set
 * under the point rather than the field-level "collisionReady" latch.
 */
export function isTerrainColliderAt(
  state: State,
  worldX: number,
  worldZ: number
): boolean {
  const rapierWorld = getRapierWorld(state);
  if (!rapierWorld) return false;
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (!data.initialized || data.chunkColliders.size === 0) continue;
    const localX = worldX - data.worldOffset.x;
    const localZ = worldZ - data.worldOffset.z;
    for (const [chunk, body] of data.chunkColliders) {
      if (!state.exists(chunk)) continue;
      const ox = TerrainChunk.originX[chunk];
      const oz = TerrainChunk.originZ[chunk];
      const half = TerrainChunk.size[chunk] * 0.5;
      if (localX < ox - half || localX > ox + half) continue;
      if (localZ < oz - half || localZ > oz + half) continue;
      if (body.numColliders() > 0) return true;
    }
  }
  return false;
}

export function findNearestTerrainEntity(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  let bestEntity = 0;
  let bestDist = Infinity;

  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    const dx = data.worldOffset.x - worldX;
    const dz = data.worldOffset.z - worldZ;
    const dist = dx * dx + dz * dz;
    if (dist < bestDist) {
      bestDist = dist;
      bestEntity = entity;
    }
  }
  return bestEntity;
}

export function setTerrainWireframe(
  state: State,
  entity: number,
  enabled: boolean
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data) return;

  Terrain.wireframe[entity] = enabled ? 1 : 0;
  data.lastWireframe = enabled ? 1 : 0;

  const registry = getChunkMeshRegistry(state);
  for (const chunk of data.chunks) {
    const mesh = registry.get(chunk);
    if (mesh) {
      (mesh.material as import('three').MeshStandardMaterial).wireframe =
        enabled;
    }
  }
}

export function reloadTerrainHeightmap(
  state: State,
  entity: number,
  url: string
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data) return;

  setTerrainHeightmapUrl(state, entity, url);
  data.heightmapUrl = url;

  const worldSize = Terrain.worldSize[entity];
  const maxHeight = Terrain.maxHeight[entity];
  loadHeightfield(url, worldSize, maxHeight)
    .then((sampler) => {
      const d = context.get(entity);
      if (!d) return;
      applyLoadedSampler(state, entity, d, sampler);
    })
    .catch((err) => {
      logger.error(`Heightmap reload failed: ${url}`, err);
    });
}

export function getTerrainStats(
  state: State,
  entity: number
): {
  activeChunks: number;
  drawCalls: number;
  totalInstances: number;
  geometries: number;
  materials: number;
  failedColliderChunks: number;
} | null {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data?.initialized) return null;

  const count = data.chunks.size;
  return {
    activeChunks: count,
    drawCalls: count,
    totalInstances: count,
    geometries: count,
    materials: count,
    failedColliderChunks: TerrainDebugInfo.failedColliderChunks[entity] ?? 0,
  };
}
