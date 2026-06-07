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
import { invalidateTerrainBvh } from '../bvh';
import {
  fireHeightmapReloadCallbacks,
  getChunkMeshRegistry,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainTextureUrl,
  setTerrainHeightmapUrl,
} from './utils';

/** Build per-chunk terrain colliders only within this radius of the player. */
const PHYSICS_COLLIDER_RADIUS = 192;

/** Re-run the (allocating) quadtree LOD selection only after the camera moves
 * this far — LOD boundaries are tens of metres apart, so per-frame reselection
 * is wasted work while standing or moving slowly. */
const LOD_RESELECT_DISTANCE = 6;
const _lastLodCam = new Map<number, { x: number; z: number }>();
let _heightmapRetryFrame = 0;

/** Shared materials per terrain field — avoids N duplicate Material instances for N chunks. */
const _sharedTerrainMaterials = new Map<number, THREE.MeshStandardMaterial>();

const terrainQuery = defineQuery([Terrain]);
const chunkQuery = defineQuery([TerrainChunk]);
const debugQuery = defineQuery([Terrain, TerrainDebugInfo]);
const mainCameraQuery = defineQuery([MainCamera, WorldTransform]);

function fieldWorldOffset(
  state: State,
  entity: number
): {
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

/** Tear down every per-chunk heightfield body for a terrain field. */
function removeChunkColliders(
  rapierWorld: RAPIER.World | null,
  data: import('./utils').TerrainEntityData
): void {
  if (rapierWorld) {
    for (const body of data.chunkColliders.values()) {
      rapierWorld.removeRigidBody(body);
    }
  }
  data.chunkColliders.clear();
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
        chunkColliders: new Map(),
      });

      if (heightmapUrl) {
        const field = entity;
        const worldSize = Terrain.worldSize[entity];
        const maxHeight = Terrain.maxHeight[entity];
        loadHeightmapFromUrl(heightmapUrl)
          .then((imgData) => {
            const data = context.get(field);
            if (!data) return;
            data.sampler = createHeightmapSampler(
              worldSize,
              maxHeight,
              imgData
            );
            invalidateTerrainBvh(state, field);
            for (const chunk of data.chunks) {
              TerrainChunk.meshDirty[chunk] = 1;
            }
            const rapierWorld = getRapierWorld(state);
            if (data.physicsBody && rapierWorld) {
              rapierWorld.removeRigidBody(data.physicsBody);
              data.physicsBody = null;
              data.physicsCollider = null;
            }
            removeChunkColliders(rapierWorld, data);
            data.collisionReady = false;
            fireHeightmapReloadCallbacks(state);
          })
          .catch((err) => {
            console.error(
              `Heightmap load failed: ${heightmapUrl} — ${err instanceof Error ? err.message : err}`
            );
          });
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
      removeChunkColliders(rapierWorld, data);
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
    for (const mat of _sharedTerrainMaterials.values()) {
      mat.dispose();
    }
    _sharedTerrainMaterials.clear();
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

      // Skip reselection if the camera barely moved (and chunks already exist).
      const last = _lastLodCam.get(fieldEntity);
      if (
        last &&
        data.chunks.size > 0 &&
        Math.hypot(localCamX - last.x, localCamZ - last.z) <
          LOD_RESELECT_DISTANCE
      ) {
        continue;
      }
      _lastLodCam.set(fieldEntity, { x: localCamX, z: localCamZ });

      const desired = selectChunks(
        worldSize,
        levels,
        ratio,
        hysteresis,
        localCamX,
        localCamZ
      );

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

      // Shallow apron just deep enough to plug LOD T-junction cracks; kept small
      // so it stays hidden below the surface instead of forming visible cliffs.
      const skirtDepth = Terrain.maxHeight[field] * Terrain.skirtWidth[field];
      // Field-constant epsilon so shared edge vertices get identical normals on
      // both neighbouring chunks (no lighting seam), independent of their LOD.
      const normalEpsilon = Terrain.worldSize[field] / 1024;

      const geometry = buildChunkGeometry(
        data.sampler,
        TerrainChunk.originX[chunk],
        TerrainChunk.originZ[chunk],
        TerrainChunk.size[chunk],
        TerrainChunk.resolution[chunk],
        skirtDepth,
        normalEpsilon
      );

      let mesh = registry.get(chunk);
      if (mesh) {
        mesh.geometry.dispose();
        mesh.geometry = geometry;
      } else {
        let material = _sharedTerrainMaterials.get(field);
        if (
          !material ||
          material.wireframe !== (Terrain.wireframe[field] === 1) ||
          material.color.getHex() !== Terrain.baseColor[field] ||
          material.roughness !== Terrain.roughness[field] ||
          material.metalness !== Terrain.metalness[field]
        ) {
          if (material) material.dispose();
          material = new THREE.MeshStandardMaterial({
            color: Terrain.baseColor[field],
            roughness: Terrain.roughness[field],
            metalness: Terrain.metalness[field],
            wireframe: Terrain.wireframe[field] === 1,
            side: THREE.DoubleSide,
          });
          _sharedTerrainMaterials.set(field, material);
        }
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

    // Retry heightmap load if sampler still flat after bootstrap (async callback
    // may have failed — ensure terrain eventually gets real data).
    _heightmapRetryFrame++;
    const _heightmapRetryInterval = 60; // retry every 60 frames (~1s at 60fps)
    if (_heightmapRetryFrame % _heightmapRetryInterval === 0) {
      for (const [entity, data] of context) {
        if (data.sampler.data !== null || !data.heightmapUrl) continue;
        loadHeightmapFromUrl(data.heightmapUrl)
          .then((imgData) => {
            data.sampler = createHeightmapSampler(
              Terrain.worldSize[entity],
              Terrain.maxHeight[entity],
              imgData
            );
            for (const chunk of data.chunks) {
              TerrainChunk.meshDirty[chunk] = 1;
            }
          })
          .catch((err) => {
            console.error(
              `Heightmap retry failed: ${data.heightmapUrl} — ${err instanceof Error ? err.message : err}`
            );
          });
      }
    }
  },
};

/**
 * Build a Rapier heightfield (column-major) for one chunk, sampled over
 * [origin ± size/2] at the chunk's mesh resolution so the collider surface is
 * identical to {@link buildChunkGeometry}. The array has (res+1)² vertices.
 * Small per-chunk fields keep each heightfield well under the size at which
 * Rapier's WASM panics on a single giant terrain-wide field.
 */
function buildChunkHeightfield(
  sampler: HeightSampler,
  originX: number,
  originZ: number,
  size: number,
  resolution: number
): { heights: Float32Array; nrows: number; ncols: number } {
  const nrows = Math.max(1, resolution);
  const ncols = nrows;
  const rows = nrows + 1;
  const cols = ncols + 1;
  const heights = new Float32Array(rows * cols);
  const half = size / 2;

  for (let col = 0; col < cols; col++) {
    const localX = originX - half + (col / ncols) * size;
    for (let row = 0; row < rows; row++) {
      const localZ = originZ - half + (row / nrows) * size;
      heights[col * rows + row] = sampleHeightAt(sampler, localX, localZ);
    }
  }

  return { heights, nrows, ncols };
}

function createChunkCollider(
  rapierWorld: RAPIER.World,
  sampler: HeightSampler,
  offset: { x: number; y: number; z: number },
  originX: number,
  originZ: number,
  size: number,
  resolution: number
): RAPIER.RigidBody {
  const { heights, nrows, ncols } = buildChunkHeightfield(
    sampler,
    originX,
    originZ,
    size,
    resolution
  );

  const body = rapierWorld.createRigidBody(
    RAPIER.RigidBodyDesc.fixed().setTranslation(
      offset.x + originX,
      offset.y,
      offset.z + originZ
    )
  );

  const colliderDesc = RAPIER.ColliderDesc.heightfield(nrows, ncols, heights, {
    x: size,
    y: 1.0,
    z: size,
  })
    .setFriction(0.7)
    .setRestitution(0.0);

  rapierWorld.createCollider(colliderDesc, body);
  return body;
}

/**
 * Per-chunk terrain physics. Before the heightmap decodes, a single flat cuboid
 * stands in so the player has ground; once the real heights are available each
 * visual LOD chunk gets its own heightfield collider (built/torn down to track
 * the chunk set), so collision matches the rendered surface everywhere.
 */
export const TerrainChunkColliderSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;

    const rapierWorld = getRapierWorld(state);
    if (!rapierWorld) return;

    const context = getTerrainContext(state);

    for (const fieldEntity of terrainQuery(state.world)) {
      const data = context.get(fieldEntity);
      if (!data || !data.initialized) continue;

      const sampler = data.sampler;
      const offset = data.worldOffset;
      const worldSize = Terrain.worldSize[fieldEntity];

      if (!sampler.data) {
        // Flat stand-in ground until the heightmap finishes decoding.
        if (!data.physicsBody) {
          const body = rapierWorld.createRigidBody(
            RAPIER.RigidBodyDesc.fixed().setTranslation(
              offset.x,
              offset.y,
              offset.z
            )
          );
          const half = worldSize / 2;
          data.physicsCollider = rapierWorld.createCollider(
            RAPIER.ColliderDesc.cuboid(half, 0.01, half)
              .setFriction(0.7)
              .setRestitution(0.0),
            body
          );
          data.physicsBody = body;
          data.collisionReady = true;
        }
        continue;
      }

      // Heights are ready: drop the flat stand-in and switch to per-chunk fields.
      if (data.physicsBody) {
        rapierWorld.removeRigidBody(data.physicsBody);
        data.physicsBody = null;
        data.physicsCollider = null;
      }

      // Only the chunks near the player need colliders — building a heightfield
      // for every visible (incl. distant) chunk wastes CPU/memory and churns as
      // far chunks change LOD. Use the camera as the player proxy.
      const cams = mainCameraQuery(state.world);
      const hasCam = cams.length > 0;
      const camLocalX = hasCam ? WorldTransform.posX[cams[0]] - offset.x : 0;
      const camLocalZ = hasCam ? WorldTransform.posZ[cams[0]] - offset.z : 0;
      const inRange = (chunk: number): boolean => {
        if (!hasCam) return true;
        const dx = TerrainChunk.originX[chunk] - camLocalX;
        const dz = TerrainChunk.originZ[chunk] - camLocalZ;
        const reach = PHYSICS_COLLIDER_RADIUS + TerrainChunk.size[chunk] * 0.5;
        return dx * dx + dz * dz <= reach * reach;
      };

      for (const chunk of data.chunks) {
        if (
          data.chunkColliders.has(chunk) ||
          !state.exists(chunk) ||
          !inRange(chunk)
        )
          continue;
        const body = createChunkCollider(
          rapierWorld,
          sampler,
          offset,
          TerrainChunk.originX[chunk],
          TerrainChunk.originZ[chunk],
          TerrainChunk.size[chunk],
          TerrainChunk.resolution[chunk]
        );
        data.chunkColliders.set(chunk, body);
      }

      for (const [chunk, body] of data.chunkColliders) {
        if (data.chunks.has(chunk) && state.exists(chunk) && inRange(chunk))
          continue;
        rapierWorld.removeRigidBody(body);
        data.chunkColliders.delete(chunk);
      }

      if (data.chunkColliders.size > 0) data.collisionReady = true;
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
      removeChunkColliders(rapierWorld, data);
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
      const rapierWorld = getRapierWorld(state);
      if (d.physicsBody && rapierWorld) {
        rapierWorld.removeRigidBody(d.physicsBody);
        d.physicsBody = null;
        d.physicsCollider = null;
      }
      removeChunkColliders(rapierWorld, d);
      d.collisionReady = false;
      fireHeightmapReloadCallbacks(state);
    })
    .catch((err) => {
      console.error(`Heightmap reload failed: ${url}`, err);
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
