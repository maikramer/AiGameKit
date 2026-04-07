import { TerrainLOD } from '@interverse/three-terrain-lod';
import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getPhysicsContext, RAPIER } from '../physics';
import { PhysicsWorldSystem } from '../physics/systems';
import { CameraSyncSystem } from '../rendering/systems';
import {
  getRenderingContext,
  getScene,
  MainCamera,
  threeCameras,
} from '../rendering';
import { TransformHierarchySystem, WorldTransform } from '../transforms';
import { Terrain, TerrainDebugInfo } from './components';
import {
  extractTerrainHeightmapImageData,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainTextureUrl,
  resampleChunkHeightsForCollider,
  setTerrainHeightmapUrl,
  type TerrainEntityData,
} from './utils';
import { WebGLTerrainMaterialProvider } from './webgl-material';

/** Set false only if colliders should match three-terrain-lod getHeightAt (canvas row order) instead of WebGL mesh. */
const COLLIDER_HEIGHT_MATCH_WEBGL = true;

const terrainQuery = defineQuery([Terrain]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);
const debugQuery = defineQuery([Terrain, TerrainDebugInfo]);

function getActiveCamera(state: State): THREE.Camera | null {
  const cameraEntities = cameraQuery(state.world);
  if (cameraEntities.length === 0) return null;
  return threeCameras.get(cameraEntities[0]) ?? null;
}

/** Collision resolution enum mapping: 0=32, 1=64, 2=128. */
function resolveCollisionResolution(raw: number): 32 | 64 | 128 {
  if (raw === 32 || raw === 128) return raw;
  return 64;
}

/**
 * Runs in `fixed` (after physics world exists) so terrain exists before
 * TerrainPhysicsSystem in the same frame — `fixed` is scheduled before `draw`.
 */
export const TerrainBootstrapSystem: System = {
  group: 'fixed',
  after: [PhysicsWorldSystem],
  update(state: State) {
    if (state.headless) return;
    const scene = getRenderingContext(state).scene;

    const context = getTerrainContext(state);
    const entities = terrainQuery(state.world);

    for (const entity of entities) {
      let data = context.get(entity);

      if (!data) {
        const heightmapUrl = getTerrainHeightmapUrl(state, entity);
        const textureUrl = getTerrainTextureUrl(state, entity);

        const terrainLOD = new TerrainLOD({
          heightMapUrl: heightmapUrl,
          textureUrl: textureUrl,
          worldSize: Terrain.worldSize[entity],
          maxHeight: Terrain.maxHeight[entity],
          levels: Terrain.levels[entity],
          resolution: Terrain.resolution[entity],
          lodDistanceRatio: Terrain.lodDistanceRatio[entity],
          wireframe: Terrain.wireframe[entity] === 1,
          heightSmoothing: Math.min(
            1,
            Math.max(0, Terrain.heightSmoothing[entity])
          ),
          heightSmoothingSpread: Math.max(
            0.25,
            Terrain.heightSmoothingSpread[entity]
          ),
        });

        // Apply runtime-configurable params
        terrainLOD.setLODHysteresis(Terrain.lodHysteresis[entity]);
        terrainLOD.setNormalStrength(Terrain.normalStrength[entity]);
        terrainLOD.setCollisionResolution(
          resolveCollisionResolution(Terrain.collisionResolution[entity])
        );
        if (Terrain.showChunkBorders[entity] === 1) {
          terrainLOD.setShowChunkBorders(true);
        }

        const materialProvider = new WebGLTerrainMaterialProvider();
        terrainLOD.setMaterialProvider(materialProvider);

        {
          const r = Terrain.resolution[entity];
          const cr: 32 | 64 | 128 = r === 32 || r === 64 || r === 128 ? r : 64;
          terrainLOD.setCollisionResolution(cr);
        }

        data = {
          terrainLOD,
          heightmapUrl,
          textureUrl,
          initialized: false,
          collisionReady: false,
          worldOffset: { x: 0, y: 0, z: 0 },
          chunkColliders: new Map(),
          materialProvider,
          lastRoughness: -1,
          lastMetalness: -1,
          lastSkirtDepth: -1,
          lastWireframe: -1,
          lastHeightSmoothing: -1,
          lastHeightSmoothingSpread: -1,
        };
        context.set(entity, data);

        scene.add(terrainLOD);

        const entityData = data;
        terrainLOD
          .init()
          .then(() => {
            entityData.initialized = true;
            // Apply material properties after init (material is created during init)
            applyMaterialProperties(entityData, entity);
          })
          .catch((err: unknown) => {
            console.error('[terrain] Failed to initialize TerrainLOD:', err);
          });
      }

      // Hot-reload: check if heightmap URL changed
      if (data) {
        const newHmUrl = getTerrainHeightmapUrl(state, entity);
        if (newHmUrl && newHmUrl !== data.heightmapUrl) {
          data.heightmapUrl = newHmUrl;
          if (data.initialized) {
            data.terrainLOD
              .loadHeightMap(newHmUrl, true)
              .then(() => {
                data.collisionReady = false;
              })
              .catch((err: unknown) => {
                console.error('[terrain] Failed to hot-reload heightmap:', err);
              });
          }
        }
        const newTexUrl = getTerrainTextureUrl(state, entity);
        if (newTexUrl && newTexUrl !== data.textureUrl) {
          data.textureUrl = newTexUrl;
        }
      }
    }

    const physicsWorld = getPhysicsContext(state).physicsWorld;
    for (const [entity, data] of context) {
      if (!state.exists(entity)) {
        if (physicsWorld) cleanupPhysics(data, physicsWorld);
        if (scene) scene.remove(data.terrainLOD);
        data.terrainLOD.dispose();
        context.delete(entity);
      }
    }
  },
  dispose(state: State) {
    const scene = getScene(state);
    const physicsWorld = getPhysicsContext(state).physicsWorld;
    const context = getTerrainContext(state);

    for (const [, data] of context) {
      if (physicsWorld) cleanupPhysics(data, physicsWorld);
      if (scene) scene.remove(data.terrainLOD);
      data.terrainLOD.dispose();
    }
    context.clear();
  },
};

/** Apply ECS component values to the Three.js material at runtime. */
function applyMaterialProperties(
  data: TerrainEntityData,
  entity: number
): void {
  const roughness = Terrain.roughness[entity];
  const metalness = Terrain.metalness[entity];
  const skirtDepth = Terrain.skirtDepth[entity];
  const wireframe = Terrain.wireframe[entity];
  const heightSmoothing = Terrain.heightSmoothing[entity];
  const heightSmoothingSpread = Terrain.heightSmoothingSpread[entity];

  // Only update when values change (avoids redundant uniform uploads)
  if (roughness !== data.lastRoughness) {
    data.materialProvider.setRoughness(roughness);
    data.lastRoughness = roughness;
  }
  if (metalness !== data.lastMetalness) {
    data.materialProvider.setMetalness(metalness);
    data.lastMetalness = metalness;
  }
  if (skirtDepth !== data.lastSkirtDepth) {
    data.materialProvider.setSkirtDepth(skirtDepth);
    data.lastSkirtDepth = skirtDepth;
  }
  if (wireframe !== data.lastWireframe) {
    data.materialProvider.setWireframe(wireframe === 1);
    data.lastWireframe = wireframe;
  }
  if (heightSmoothing !== data.lastHeightSmoothing) {
    data.terrainLOD.setHeightSmoothing(heightSmoothing);
    data.lastHeightSmoothing = heightSmoothing;
  }
  if (heightSmoothingSpread !== data.lastHeightSmoothingSpread) {
    data.terrainLOD.setHeightSmoothingSpread(heightSmoothingSpread);
    data.lastHeightSmoothingSpread = heightSmoothingSpread;
  }
}

/** LOD / frustum updates — must run after camera is updated (draw group). */
export const TerrainRenderSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const context = getTerrainContext(state);
    const camera = getActiveCamera(state);
    if (!camera) return;

    for (const entity of terrainQuery(state.world)) {
      const data = context.get(entity);
      if (!data) continue;
      if (state.hasComponent(entity, WorldTransform)) {
        const ox = WorldTransform.posX[entity];
        const oy = WorldTransform.posY[entity];
        const oz = WorldTransform.posZ[entity];
        data.terrainLOD.position.set(ox, oy, oz);
        data.worldOffset = { x: ox, y: oy, z: oz };
      }
      data.terrainLOD.update(camera);

      // Apply material property changes every frame (cheap equality check)
      if (data.initialized) {
        applyMaterialProperties(data, entity);
      }
    }
  },
};

/** Populates TerrainDebugInfo component with live terrain statistics. */
export const TerrainDebugSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    const context = getTerrainContext(state);
    const now = state.time.elapsed;

    for (const entity of debugQuery(state.world)) {
      const data = context.get(entity);
      if (!data || !data.initialized) continue;

      const stats = data.terrainLOD.getStats();
      TerrainDebugInfo.activeChunks[entity] = stats.instances.active;
      TerrainDebugInfo.drawCalls[entity] = stats.drawCalls;
      TerrainDebugInfo.totalInstances[entity] = stats.instances.total;
      TerrainDebugInfo.geometryCount[entity] = stats.geometries;
      TerrainDebugInfo.materialCount[entity] = stats.materials;
      TerrainDebugInfo.lastUpdated[entity] = now;
    }
  },
};

export const TerrainPhysicsSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state: State) {
    const physicsCtx = getPhysicsContext(state);
    const physicsWorld = physicsCtx.physicsWorld;
    if (!physicsWorld) return;

    const context = getTerrainContext(state);
    const entities = terrainQuery(state.world);

    for (const entity of entities) {
      const data = context.get(entity);
      if (!data || !data.initialized || data.collisionReady) continue;

      let ox = 0;
      let oy = 0;
      let oz = 0;
      if (state.hasComponent(entity, WorldTransform)) {
        ox = WorldTransform.posX[entity];
        oy = WorldTransform.posY[entity];
        oz = WorldTransform.posZ[entity];
      }
      data.worldOffset = { x: ox, y: oy, z: oz };
      const offset = data.worldOffset;

      setupCollisionCallbacks(data, physicsWorld);
      data.collisionReady = true;

      const terrainLOD = data.terrainLOD;
      const worldSize = terrainLOD.getConfig().worldSize;
      const maxHeight = terrainLOD.getConfig().maxHeight;

      terrainLOD
        .computeAllCollisionData()
        .then(() => {
          const imageData = extractTerrainHeightmapImageData(terrainLOD);
          const allData = terrainLOD.getAllCollisionData();
          for (const [, chunkData] of allData) {
            if (imageData && COLLIDER_HEIGHT_MATCH_WEBGL) {
              resampleChunkHeightsForCollider(
                chunkData,
                worldSize,
                maxHeight,
                imageData,
                true
              );
            }
          }
          for (const [key, chunkData] of allData) {
            if (!data.chunkColliders.has(key)) {
              const result = createChunkCollider(
                chunkData,
                physicsWorld,
                offset
              );
              if (result) data.chunkColliders.set(key, result);
            }
          }
        })
        .catch((err: unknown) => {
          console.error('[terrain] Failed to compute collision data:', err);
          data.collisionReady = false;
        });
    }
  },
  dispose(state: State) {
    const physicsCtx = getPhysicsContext(state);
    const physicsWorld = physicsCtx.physicsWorld;
    const context = getTerrainContext(state);

    if (physicsWorld) {
      for (const [, data] of context) {
        cleanupPhysics(data, physicsWorld);
      }
    }
  },
};

function setupCollisionCallbacks(
  data: TerrainEntityData,
  physicsWorld: RAPIER.World
): void {
  data.terrainLOD.setCollisionCallback({
    onChunkEnterLOD0(chunk) {
      const key = `${chunk.index.x}_${chunk.index.z}`;
      if (data.chunkColliders.has(key)) return;

      const worldSize = data.terrainLOD.getConfig().worldSize;
      const maxHeight = data.terrainLOD.getConfig().maxHeight;
      const imageData = extractTerrainHeightmapImageData(data.terrainLOD);
      if (imageData && COLLIDER_HEIGHT_MATCH_WEBGL) {
        resampleChunkHeightsForCollider(
          chunk,
          worldSize,
          maxHeight,
          imageData,
          true
        );
      }

      const result = createChunkCollider(chunk, physicsWorld, data.worldOffset);
      if (result) data.chunkColliders.set(key, result);
    },
    onChunkExitLOD0(index) {
      const key = `${index.x}_${index.z}`;
      const existing = data.chunkColliders.get(key);
      if (existing) {
        physicsWorld.removeCollider(existing.collider, false);
        physicsWorld.removeRigidBody(existing.body);
        data.chunkColliders.delete(key);
      }
    },
  });
}

/**
 * Parry/Rapier heightfields use a `heights_zx` matrix: row index → Z, column index → X
 * (see parry3d `HeightField::new` / `triangles_vids_at`: `p00 = i + j * nrows`).
 * Column-major flat layout: index = i_z + j_x * nrows_z.
 * three-terrain-lod uses row-major `rowZ * cols + colX` for the same (Z,X) samples.
 */
function terrainHeightsToParryColumnMajor(
  terrainRowMajor: Float32Array,
  vertexCountZ: number,
  vertexCountX: number
): Float32Array {
  const out = new Float32Array(vertexCountZ * vertexCountX);
  for (let rowZ = 0; rowZ < vertexCountZ; rowZ++) {
    for (let colX = 0; colX < vertexCountX; colX++) {
      out[rowZ + colX * vertexCountZ] =
        terrainRowMajor[rowZ * vertexCountX + colX];
    }
  }
  return out;
}

function createChunkCollider(
  chunk: {
    position: { x: number; y: number; z: number };
    size: number;
    rows: number;
    cols: number;
    heights: Float32Array;
    scale: { x: number; y: number; z: number };
  },
  physicsWorld: RAPIER.World,
  offset: { x: number; y: number; z: number }
): { body: RAPIER.RigidBody; collider: RAPIER.Collider } | null {
  try {
    const nrows = chunk.rows;
    const ncols = chunk.cols;
    const segAlongZ = nrows - 1;
    const segAlongX = ncols - 1;

    const heightsColMajor = terrainHeightsToParryColumnMajor(
      chunk.heights,
      nrows,
      ncols
    );

    const expectedLen = (segAlongZ + 1) * (segAlongX + 1);
    if (heightsColMajor.length !== expectedLen) {
      console.error(
        '[terrain] Heightfield size mismatch:',
        heightsColMajor.length,
        'vs expected',
        expectedLen,
        `(segZ=${segAlongZ}, segX=${segAlongX})`
      );
      return null;
    }

    const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(
      chunk.position.x + offset.x,
      chunk.position.y + offset.y,
      chunk.position.z + offset.z
    );
    const body = physicsWorld.createRigidBody(bodyDesc);

    const scale = new RAPIER.Vector3(
      chunk.scale.x,
      chunk.scale.y,
      chunk.scale.z
    );
    const colliderDesc = RAPIER.ColliderDesc.heightfield(
      segAlongZ,
      segAlongX,
      heightsColMajor,
      scale
    );
    colliderDesc.setFriction(0.8);
    colliderDesc.setRestitution(0.0);

    const collider = physicsWorld.createCollider(colliderDesc, body);
    return { body, collider };
  } catch (err) {
    console.error('[terrain] Failed to create chunk collider:', err);
    console.error(
      '[terrain] Chunk debug:',
      JSON.stringify({
        rows: chunk.rows,
        cols: chunk.cols,
        heightsLen: chunk.heights.length,
        scale: chunk.scale,
        position: chunk.position,
        size: chunk.size,
      })
    );
    return null;
  }
}

function cleanupPhysics(
  data: TerrainEntityData,
  physicsWorld: RAPIER.World
): void {
  for (const [, { body, collider }] of data.chunkColliders) {
    physicsWorld.removeCollider(collider, false);
    physicsWorld.removeRigidBody(body);
  }
  data.chunkColliders.clear();
}

// ─────────────────────────────────────────────────────────────────────────────
// Public query helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Sample terrain height at a world position.
 * Returns 0 if no terrain entity is initialized.
 */
export function getTerrainHeightAt(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (data.initialized) {
      return data.terrainLOD.getHeightAt(worldX, worldZ);
    }
  }
  return 0;
}

/**
 * Find the nearest initialized terrain entity to a given world position.
 * Returns 0 if no terrain is available.
 */
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

/**
 * Toggle wireframe on a specific terrain entity at runtime.
 * No-op if the entity has no terrain data.
 */
export function setTerrainWireframe(
  state: State,
  entity: number,
  enabled: boolean
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (data?.initialized) {
    data.terrainLOD.setWireframe(enabled);
    data.materialProvider.setWireframe(enabled);
    data.lastWireframe = enabled ? 1 : 0;
  }
}

/**
 * Hot-reload the heightmap on a terrain entity from a new URL.
 * Invalidates physics colliders after loading.
 */
export function reloadTerrainHeightmap(
  state: State,
  entity: number,
  url: string
): void {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data?.initialized) return;

  setTerrainHeightmapUrl(state, entity, url);
  data.heightmapUrl = url;
  data.terrainLOD
    .loadHeightMap(url, true)
    .then(() => {
      data.collisionReady = false;
    })
    .catch((err: unknown) => {
      console.error('[terrain] Failed to hot-reload heightmap:', err);
    });
}

/**
 * Get live terrain statistics for a terrain entity.
 * Returns null if the entity has no terrain data or is not initialized.
 */
export function getTerrainStats(
  state: State,
  entity: number
): {
  activeChunks: number;
  drawCalls: number;
  totalInstances: number;
  geometries: number;
  materials: number;
} | null {
  const context = getTerrainContext(state);
  const data = context.get(entity);
  if (!data?.initialized) return null;

  const stats = data.terrainLOD.getStats();
  return {
    activeChunks: stats.instances.active,
    drawCalls: stats.drawCalls,
    totalInstances: stats.instances.total,
    geometries: stats.geometries,
    materials: stats.materials,
  };
}
