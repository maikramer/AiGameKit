import { TerrainLOD } from '@interverse/three-terrain-lod';
import type * as THREE from 'three';
import type { State, System } from '../../core';
import { defineQuery } from '../../core';
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
import { Terrain } from './components';
import {
  extractTerrainHeightmapImageData,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainTextureUrl,
  resampleChunkHeightsForCollider,
  type TerrainEntityData,
} from './utils';
import { WebGLTerrainMaterialProvider } from './webgl-material';

/** Set false only if colliders should match three-terrain-lod getHeightAt (canvas row order) instead of WebGL mesh. */
const COLLIDER_HEIGHT_MATCH_WEBGL = true;

const terrainQuery = defineQuery([Terrain]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);

function getActiveCamera(state: State): THREE.Camera | null {
  const cameraEntities = cameraQuery(state.world);
  if (cameraEntities.length === 0) return null;
  return threeCameras.get(cameraEntities[0]) ?? null;
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
        });

        // DefaultTerrainMaterial in three-terrain-lod is WebGPU/TSL; WebGL needs this provider.
        // Do not wait for renderer: TerrainBootstrap runs in `fixed` before the first `draw`
        // where WebGLRenderer is created — if we skip here, Node materials never compile in WebGL.
        terrainLOD.setMaterialProvider(new WebGLTerrainMaterialProvider());
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
        };
        context.set(entity, data);

        scene.add(terrainLOD);

        const entityData = data;
        terrainLOD
          .init()
          .then(() => {
            entityData.initialized = true;
          })
          .catch((err: unknown) => {
            console.error('[terrain] Failed to initialize TerrainLOD:', err);
          });
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
