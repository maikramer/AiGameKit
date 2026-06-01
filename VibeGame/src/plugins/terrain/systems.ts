import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, MainCamera } from '../rendering';
import { WorldTransform } from '../transforms';
import { getRapierWorld } from '../physics';
import { Terrain, TerrainChunk, TerrainDebugInfo } from './components';
import { buildChunkGeometry } from './chunk-geometry';
import {
  createFlatSampler,
  createHeightmapSampler,
  loadHeightmapFromUrl,
  sampleHeightAt,
} from './height-sampler';
import type { HeightSampler } from './height-sampler';
import { chunkKey, resolutionForLevel, selectChunks } from './lod-select';
import {
  fireHeightmapReloadCallbacks,
  getChunkMeshRegistry,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainTextureUrl,
  setTerrainHeightmapUrl,
} from './utils';

const terrainQuery = defineQuery([Terrain]);
const chunkQuery = defineQuery([TerrainChunk]);
const debugQuery = defineQuery([Terrain, TerrainDebugInfo]);
const mainCameraQuery = defineQuery([MainCamera, WorldTransform]);

function fieldWorldOffset(state: State, entity: number): {
  x: number;
  y: number;
  z: number;
} {
  if (state.hasComponent(entity, WorldTransform)) {
    return {
      x: WorldTransform.posX[entity],
      y: WorldTransform.posY[entity],
      z: WorldTransform.posZ[entity],
    };
  }
  return { x: 0, y: 0, z: 0 };
}

export const TerrainFieldBootstrapSystem: System = {
  group: 'fixed',
  update(state: State) {
    if (state.headless) return;

    const context = getTerrainContext(state);

    for (const entity of terrainQuery(state.world)) {
      if (context.has(entity)) continue;

      const sampler = createFlatSampler(
        Terrain.worldSize[entity],
        Terrain.maxHeight[entity]
      );

      const heightmapUrl = getTerrainHeightmapUrl(state, entity);

      context.set(entity, {
        sampler,
        chunks: new Set<number>(),
        heightmapUrl,
        textureUrl: getTerrainTextureUrl(state, entity),
        initialized: true,
        collisionReady: false,
        worldOffset: fieldWorldOffset(state, entity),
        lastWireframe: Terrain.wireframe[entity],
        lastShowChunkBorders: Terrain.showChunkBorders[entity],
        physicsBody: null,
        physicsCollider: null,
      });

      if (heightmapUrl) {
        const field = entity;
        const worldSize = Terrain.worldSize[entity];
        const maxHeight = Terrain.maxHeight[entity];
        loadHeightmapFromUrl(heightmapUrl)
          .then((imgData) => {
            const data = context.get(field);
            if (!data) return;
            data.sampler = createHeightmapSampler(worldSize, maxHeight, imgData);
            for (const chunk of data.chunks) {
              TerrainChunk.meshDirty[chunk] = 1;
            }
            if (data.physicsBody) {
              const rapierWorld = getRapierWorld(state);
              if (rapierWorld) {
                rapierWorld.removeRigidBody(data.physicsBody);
                data.physicsBody = null;
                data.physicsCollider = null;
                data.collisionReady = false;
              }
            }
            fireHeightmapReloadCallbacks(state);
          })
          .catch(() => {});
      }
    }

    for (const [entity, data] of context) {
      if (state.exists(entity)) continue;
      const rapierWorld = getRapierWorld(state);
      if (rapierWorld && data.physicsBody) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }
      for (const chunk of data.chunks) {
        if (state.exists(chunk)) state.destroyEntity(chunk);
      }
      context.delete(entity);
    }
  },
  dispose(state: State) {
    const scene = getRenderingContext(state).scene;
    const registry = getChunkMeshRegistry(state);
    for (const [chunk, mesh] of registry) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      registry.delete(chunk);
    }
    getTerrainContext(state).clear();
  },
};

export const TerrainLodSelectSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;

    const context = getTerrainContext(state);
    const cameras = mainCameraQuery(state.world);
    if (cameras.length === 0) return;
    const camEntity = cameras[0];
    const camX = WorldTransform.posX[camEntity];
    const camZ = WorldTransform.posZ[camEntity];

    for (const fieldEntity of terrainQuery(state.world)) {
      const data = context.get(fieldEntity);
      if (!data || !data.initialized) continue;

      const worldSize = Terrain.worldSize[fieldEntity];
      const levels = Terrain.levels[fieldEntity];
      const ratio = Terrain.lodDistanceRatio[fieldEntity];
      const hysteresis = Terrain.lodHysteresis[fieldEntity];
      const baseResolution = Terrain.resolution[fieldEntity];

      const offset = data.worldOffset;
      const localCamX = camX - offset.x;
      const localCamZ = camZ - offset.z;

      const desired = selectChunks(worldSize, levels, ratio, hysteresis, localCamX, localCamZ);

      const desiredKeys = new Map<string, (typeof desired)[number]>();
      for (const desc of desired) {
        desiredKeys.set(chunkKey(desc), desc);
      }

      const existingKeys = new Map<string, number>();
      for (const chunkEid of data.chunks) {
        if (!state.exists(chunkEid)) continue;
        const key = `${TerrainChunk.originX[chunkEid]},${TerrainChunk.originZ[chunkEid]},${TerrainChunk.level[chunkEid]}`;
        existingKeys.set(key, chunkEid);
      }

      for (const [key, desc] of desiredKeys) {
        if (existingKeys.has(key)) continue;

        const chunk = state.createEntity();
        const res = resolutionForLevel(baseResolution, desc.level);
        state.addComponent(chunk, TerrainChunk, {
          field: fieldEntity,
          originX: desc.originX,
          originZ: desc.originZ,
          size: desc.size,
          level: desc.level,
          resolution: res,
          meshDirty: 1,
        });
        data.chunks.add(chunk);
      }

      for (const [key, chunkEid] of existingKeys) {
        if (desiredKeys.has(key)) continue;
        data.chunks.delete(chunkEid);
        if (state.exists(chunkEid)) {
          state.destroyEntity(chunkEid);
        }
      }
    }
  },
};

export const TerrainMeshSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

    const scene = getRenderingContext(state).scene;
    const registry = getChunkMeshRegistry(state);
    const context = getTerrainContext(state);

    for (const [chunk, mesh] of registry) {
      if (state.exists(chunk)) continue;
      scene.remove(mesh);
      mesh.geometry.dispose();
      registry.delete(chunk);
    }

    for (const chunk of chunkQuery(state.world)) {
      if (TerrainChunk.meshDirty[chunk] !== 1) continue;

      const field = TerrainChunk.field[chunk];
      const data = context.get(field);
      if (!data) continue;

      const geometry = buildChunkGeometry(
        data.sampler,
        TerrainChunk.originX[chunk],
        TerrainChunk.originZ[chunk],
        TerrainChunk.size[chunk],
        TerrainChunk.resolution[chunk]
      );

      let mesh = registry.get(chunk);
      if (mesh) {
        mesh.geometry.dispose();
        mesh.geometry = geometry;
      } else {
        const material = new THREE.MeshStandardMaterial({
          color: Terrain.baseColor[field],
          roughness: Terrain.roughness[field],
          metalness: Terrain.metalness[field],
          wireframe: Terrain.wireframe[field] === 1,
        });
        mesh = new THREE.Mesh(geometry, material);
        mesh.receiveShadow = true;
        mesh.castShadow = false;
        registry.set(chunk, mesh);
        scene.add(mesh);
      }

      const offset = data.worldOffset;
      mesh.position.set(
        offset.x + TerrainChunk.originX[chunk],
        offset.y,
        offset.z + TerrainChunk.originZ[chunk]
      );

      TerrainChunk.meshDirty[chunk] = 0;
    }
  },
};

/**
 * Build a terrain-wide heightfield in Rapier's column-major format directly.
 * Eliminates the intermediate row-major array and transpose step.
 * nrows/ncols define the cell count; the array has (nrows+1)*(ncols+1) vertices.
 */
function buildTerrainHeightfieldDirect(
  sampler: HeightSampler,
  worldSize: number,
  resolution: number
): { heights: Float32Array; nrows: number; ncols: number } {
  const nrows = resolution;
  const ncols = resolution;
  const rows = nrows + 1;
  const cols = ncols + 1;
  const heights = new Float32Array(rows * cols);
  const half = worldSize / 2;

  for (let col = 0; col < cols; col++) {
    const localX = -half + (col / ncols) * worldSize;
    for (let row = 0; row < rows; row++) {
      const localZ = -half + (row / nrows) * worldSize;
      heights[col * rows + row] = sampleHeightAt(sampler, localX, localZ);
    }
  }

  return { heights, nrows, ncols };
}

export const TerrainPhysicsSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;

    const rapierWorld = getRapierWorld(state);
    if (!rapierWorld) return;

    const context = getTerrainContext(state);

    for (const fieldEntity of terrainQuery(state.world)) {
      const data = context.get(fieldEntity);
      if (!data || !data.initialized) continue;
      if (data.physicsBody) continue;

      const sampler = data.sampler;
      const worldSize = Terrain.worldSize[fieldEntity];
      const offset = data.worldOffset;

      if (!sampler.data) {
        const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(
          offset.x,
          offset.y,
          offset.z
        );
        const body = rapierWorld.createRigidBody(bodyDesc);
        const half = worldSize / 2;
        const colliderDesc = RAPIER.ColliderDesc.cuboid(half, 0.01, half)
          .setFriction(0.7)
          .setRestitution(0.0);
        const collider = rapierWorld.createCollider(colliderDesc, body);
        data.physicsBody = body;
        data.physicsCollider = collider;
        data.collisionReady = true;
        continue;
      }

      const collisionRes = Terrain.collisionResolution[fieldEntity];

      const { heights, nrows, ncols } = buildTerrainHeightfieldDirect(
        sampler,
        worldSize,
        collisionRes
      );

      const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(
        offset.x,
        offset.y,
        offset.z
      );
      const body = rapierWorld.createRigidBody(bodyDesc);

      const colliderDesc = RAPIER.ColliderDesc.heightfield(
        nrows,
        ncols,
        heights,
        { x: worldSize, y: 1.0, z: worldSize }
      )
        .setFriction(0.7)
        .setRestitution(0.0);

      const collider = rapierWorld.createCollider(colliderDesc, body);
      data.physicsBody = body;
      data.physicsCollider = collider;
      data.collisionReady = true;
    }
  },
  dispose(state: State) {
    const rapierWorld = getRapierWorld(state);
    if (!rapierWorld) return;
    const context = getTerrainContext(state);
    for (const [, data] of context) {
      if (data.physicsBody) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }
    }
  },
};

export const TerrainDebugSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    const context = getTerrainContext(state);
    const now = state.time.elapsed;

    for (const entity of debugQuery(state.world)) {
      const data = context.get(entity);
      if (!data || !data.initialized) continue;

      const count = data.chunks.size;
      TerrainDebugInfo.activeChunks[entity] = count;
      TerrainDebugInfo.drawCalls[entity] = count;
      TerrainDebugInfo.totalInstances[entity] = count;
      TerrainDebugInfo.geometryCount[entity] = count;
      TerrainDebugInfo.materialCount[entity] = count;
      TerrainDebugInfo.lastUpdated[entity] = now;
    }
  },
};

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
      (mesh.material as THREE.MeshStandardMaterial).wireframe = enabled;
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
  loadHeightmapFromUrl(url)
    .then((imgData) => {
      const d = context.get(entity);
      if (!d) return;
      d.sampler = createHeightmapSampler(worldSize, maxHeight, imgData);
      for (const chunk of d.chunks) {
        TerrainChunk.meshDirty[chunk] = 1;
      }
      if (d.physicsBody) {
        const rapierWorld = getRapierWorld(state);
        if (rapierWorld) {
          rapierWorld.removeRigidBody(d.physicsBody);
          d.physicsBody = null;
          d.physicsCollider = null;
          d.collisionReady = false;
        }
      }
      fireHeightmapReloadCallbacks(state);
    })
    .catch(() => {});
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
