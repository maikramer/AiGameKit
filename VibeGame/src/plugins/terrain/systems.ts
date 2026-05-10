import { TerrainLOD } from '@interverse/three-terrain-lod';
import { DefaultTerrainMaterial } from '@interverse/three-terrain-lod';
import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { getPhysicsContext, RAPIER } from '../physics';
import { PhysicsWorldSystem } from '../physics/systems';
import { CameraSyncSystem } from '../rendering/systems';
import { getRenderingContext, MainCamera, threeCameras } from '../rendering';
import { TransformHierarchySystem, WorldTransform } from '../transforms';
import { Terrain, TerrainDebugInfo } from './components';
import {
  fireHeightmapReloadCallbacks,
  getTerrainContext,
  getTerrainHeightmapUrl,
  getTerrainTextureUrl,
  setTerrainHeightmapUrl,
  terrainHeightsToRapierColumnMajor,
  type TerrainEntityData,
} from './utils';
import {
  loadTerrainData,
  spawnWaterEntitiesFromTerrainData,
} from './terrain-data-loader';

const terrainQuery = defineQuery([Terrain]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);
const debugQuery = defineQuery([Terrain, TerrainDebugInfo]);

function getActiveCamera(state: State): THREE.Camera | null {
  const cameraEntities = cameraQuery(state.world);
  if (cameraEntities.length === 0) return null;
  return threeCameras.get(cameraEntities[0]) ?? null;
}

function resolveCollisionResolution(raw: number): 32 | 64 | 128 {
  if (raw === 32 || raw === 128) return raw;
  return 64;
}

function hexToRgb(hex: number): [number, number, number] {
  const h = hex >>> 0;
  return [((h >> 16) & 0xff) / 255, ((h >> 8) & 0xff) / 255, (h & 0xff) / 255];
}

function applyMaterialCustomization(
  terrainLOD: TerrainLOD,
  entity: number
): void {
  const provider = (terrainLOD as any).defaultMaterialProvider as
    | DefaultTerrainMaterial
    | undefined;
  if (!provider) return;

  provider.setSlopeThreshold(Terrain.slopeThreshold[entity]);
  provider.setSlopeSoftness(Terrain.slopeSoftness[entity]);
  provider.setSnowHeight(Terrain.snowHeight[entity]);
  provider.setNormalStrength(Terrain.normalStrength[entity]);
  provider.setSkirtDepth(Terrain.skirtDepth[entity]);
  provider.setSkirtWidth(Terrain.skirtWidth[entity]);
  provider.setHeightSmoothing(Terrain.heightSmoothing[entity]);
  provider.setHeightSmoothingSpread(Terrain.heightSmoothingSpread[entity]);

  const nodes = provider.getNodes();
  if (nodes.colorHigh) {
    const [r, g, b] = hexToRgb(Terrain.colorHigh[entity]);
    nodes.colorHigh.value.set(r, g, b);
  }
  if (nodes.colorMid) {
    const [r, g, b] = hexToRgb(Terrain.colorMid[entity]);
    nodes.colorMid.value.set(r, g, b);
  }
  if (nodes.colorLow) {
    const [r, g, b] = hexToRgb(Terrain.colorLow[entity]);
    nodes.colorLow.value.set(r, g, b);
  }
  if (nodes.colorRock) {
    const [r, g, b] = hexToRgb(Terrain.colorRock[entity]);
    nodes.colorRock.value.set(r, g, b);
  }
}

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
          normalStrength: Math.max(0, Terrain.normalStrength[entity]),
          skirtDepth: Terrain.skirtDepth[entity],
          skirtWidth: Terrain.skirtWidth[entity],
          showChunkBorders: Terrain.showChunkBorders[entity] === 1,
        });

        terrainLOD.setLODHysteresis(Terrain.lodHysteresis[entity]);
        terrainLOD.setCollisionResolution(
          resolveCollisionResolution(Terrain.collisionResolution[entity])
        );

        data = {
          terrainLOD,
          heightmapUrl,
          textureUrl,
          initialized: false,
          collisionReady: false,
          collisionDispatchStarted: false,
          worldOffset: { x: 0, y: 0, z: 0 },
          chunkColliders: new Map(),
          lastWireframe: Terrain.wireframe[entity],
          lastShowChunkBorders: Terrain.showChunkBorders[entity],
        };
        context.set(entity, data);

        scene.add(terrainLOD);

        const entityData = data;
        terrainLOD
          .init()
          .then(async () => {
            entityData.initialized = true;

            replaceProceduralDiffuse(terrainLOD);

            applyMaterialCustomization(terrainLOD, entity);

            terrainLOD.traverse((child) => {
              const mesh = child as THREE.Mesh;
              if (mesh.isMesh === true) {
                mesh.receiveShadow = true;
                mesh.castShadow = false;
              }
            });

            patchBilinearGetHeightAt(terrainLOD);

            await terrainLOD.computeAllCollisionData().catch((err: unknown) => {
              console.error('[terrain] computeAllCollisionData failed:', err);
            });

            const hmUrl = getTerrainHeightmapUrl(state, entity);
            if (hmUrl) {
              const terrainJsonUrl = hmUrl.replace(/[^/]+$/, 'terrain.json');
              loadTerrainData(terrainJsonUrl)
                .then((terrainData) => {
                  spawnWaterEntitiesFromTerrainData(state, terrainData);
                })
                .catch((err: unknown) => {
                  console.warn(
                    '[terrain] Failed to spawn water entities:',
                    err
                  );
                });
            }
          })
          .catch((err: unknown) => {
            console.error('[terrain] Failed to initialize TerrainLOD:', err);
          });
      }

      if (data) {
        const newHmUrl = getTerrainHeightmapUrl(state, entity);
        if (newHmUrl && newHmUrl !== data.heightmapUrl) {
          data.heightmapUrl = newHmUrl;
          if (data.initialized) {
            data.terrainLOD
              .loadHeightMap(newHmUrl, true)
              .then(() => {
                const physicsWorld = getPhysicsContext(state).physicsWorld;
                if (physicsWorld) cleanupPhysics(data, physicsWorld);
                data.collisionDispatchStarted = false;
                data.collisionReady = false;
                fireHeightmapReloadCallbacks(state);
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
    const scene = getRenderingContext(state).scene;
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

      if (data.initialized) {
        const wf = Terrain.wireframe[entity];
        if (wf !== data.lastWireframe) {
          data.terrainLOD.setWireframe(wf === 1);
          data.lastWireframe = wf;
        }
        const scb = Terrain.showChunkBorders[entity];
        if (scb !== data.lastShowChunkBorders) {
          data.terrainLOD.setShowChunkBorders(scb === 1);
          data.lastShowChunkBorders = scb;
        }
        const im = (data.terrainLOD as any).instancedMesh as
          | THREE.InstancedMesh
          | undefined;
        if (im) {
          im.receiveShadow = true;
          im.castShadow = false;
        }
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
    const context = getTerrainContext(state);
    const entities = terrainQuery(state.world);

    if (!physicsWorld) {
      for (const entity of entities) {
        const data = context.get(entity);
        if (data?.initialized) data.collisionReady = true;
      }
      return;
    }

    for (const entity of entities) {
      const data = context.get(entity);
      if (!data || !data.initialized || data.collisionDispatchStarted) continue;

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
      data.collisionDispatchStarted = true;

      const terrainLOD = data.terrainLOD;
      patchBilinearGetHeightAt(terrainLOD);

      terrainLOD
        .computeAllCollisionData()
        .then((allData) => {
          for (const [key, chunkData] of allData) {
            if (!data.chunkColliders.has(key)) {
              const result = createChunkCollider(
                chunkData,
                physicsWorld,
                offset,
                entity
              );
              if (result) data.chunkColliders.set(key, result);
            }
          }
          data.collisionReady = true;
        })
        .catch((err: unknown) => {
          console.error('[terrain] Failed to compute collision data:', err);
          data.collisionReady = false;
          data.collisionDispatchStarted = false;
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

function replaceProceduralDiffuse(terrainLOD: TerrainLOD): void {
  const tlod = terrainLOD as any;
  if (tlod.proceduralDiffuseTexture) {
    tlod.proceduralDiffuseTexture.dispose();
    const canvas = document.createElement('canvas');
    canvas.width = 4;
    canvas.height = 4;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#3e6b29';
    ctx.fillRect(0, 0, 4, 4);
    const solid = new THREE.CanvasTexture(canvas);
    solid.wrapS = solid.wrapT = THREE.RepeatWrapping;
    solid.repeat.set(16, 16);
    solid.anisotropy = 16;
    tlod.diffuseTexture = solid;
    tlod.proceduralDiffuseTexture = null;
    tlod.recreateMaterial();
  }
}

function patchBilinearGetHeightAt(terrainLOD: TerrainLOD): void {
  const tlod = terrainLOD as any;
  if (tlod._bilinearPatched) return;
  tlod._bilinearPatched = true;

  const sample = (imgData: ImageData, u: number, v: number): number => {
    const w = imgData.width;
    const d = imgData.data;
    const px = u * (w - 1);
    const py = v * (imgData.height - 1);
    const x0 = Math.floor(px);
    const y0 = Math.floor(py);
    const x1 = Math.min(x0 + 1, w - 1);
    const y1 = Math.min(y0 + 1, imgData.height - 1);
    const fx = px - x0;
    const fy = py - y0;
    const h00 = d[(y0 * w + x0) * 4] / 255;
    const h10 = d[(y0 * w + x1) * 4] / 255;
    const h01 = d[(y1 * w + x0) * 4] / 255;
    const h11 = d[(y1 * w + x1) * 4] / 255;
    return (
      h00 * (1 - fx) * (1 - fy) +
      h10 * fx * (1 - fy) +
      h01 * (1 - fx) * fy +
      h11 * fx * fy
    );
  };

  tlod.getHeightAt = (worldX: number, worldZ: number): number => {
    const imgData = tlod.heightmapImageData;
    if (!imgData) return 0;

    const config = tlod.config;
    const halfWorld = config.worldSize / 2;
    const u = (worldX + halfWorld) / config.worldSize;
    const v = (worldZ + halfWorld) / config.worldSize;
    if (u < 0 || u > 1 || v < 0 || v > 1) return 0;

    const rawH = sample(imgData, u, v);

    const smoothing = config.heightSmoothing;
    if (smoothing <= 0) return rawH * config.maxHeight;

    const w = imgData.width;
    const h = imgData.height;
    const spread = config.heightSmoothingSpread;
    const sU = spread / w;
    const sV = spread / h;

    const hN = sample(imgData, u, v - sV);
    const hS = sample(imgData, u, v + sV);
    const hE = sample(imgData, u + sU, v);
    const hW = sample(imgData, u - sU, v);
    const hNE = sample(imgData, u + sU, v - sV);
    const hNW = sample(imgData, u - sU, v - sV);
    const hSE = sample(imgData, u + sU, v + sV);
    const hSW = sample(imgData, u - sU, v + sV);

    const filtered =
      (rawH * 4 + (hN + hS + hE + hW) * 2 + (hNE + hNW + hSE + hSW)) / 16;

    return (rawH * (1 - smoothing) + filtered * smoothing) * config.maxHeight;
  };
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
  offset: { x: number; y: number; z: number },
  terrainEntity: number = 0
): { body: RAPIER.RigidBody; collider: RAPIER.Collider } | null {
  try {
    const vertexRows = chunk.rows;
    const vertexCols = chunk.cols;
    const subdivRows = vertexRows - 1;
    const subdivCols = vertexCols - 1;

    const heightsColMajor = terrainHeightsToRapierColumnMajor(
      chunk.heights,
      vertexRows,
      vertexCols
    );

    const expectedLen = vertexRows * vertexCols;
    if (heightsColMajor.length !== expectedLen) {
      console.error(
        '[terrain] Heightfield size mismatch:',
        heightsColMajor.length,
        'vs expected',
        expectedLen,
        `(vertexRows=${vertexRows}, vertexCols=${vertexCols})`
      );
      return null;
    }

    const bodyX = chunk.position.x + offset.x;
    const bodyZ = chunk.position.z + offset.z;
    const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(
      bodyX,
      chunk.position.y + offset.y,
      bodyZ
    );
    const body = physicsWorld.createRigidBody(bodyDesc);

    const scale = new RAPIER.Vector3(
      chunk.scale.x,
      chunk.scale.y,
      chunk.scale.z
    );
    const colliderDesc = RAPIER.ColliderDesc.heightfield(
      subdivRows,
      subdivCols,
      heightsColMajor,
      scale
    );
    colliderDesc.setFriction(0.8);
    colliderDesc.setRestitution(0.0);

    const collider = physicsWorld.createCollider(colliderDesc, body);

    if (Math.abs(chunk.position.x) < chunk.size && Math.abs(chunk.position.z) < chunk.size) {
      let hMin = Infinity, hMax = -Infinity;
      for (let i = 0; i < chunk.heights.length; i++) {
        if (chunk.heights[i] < hMin) hMin = chunk.heights[i];
        if (chunk.heights[i] > hMax) hMax = chunk.heights[i];
      }
      const mid = Math.floor(chunk.rows / 2) * chunk.cols + Math.floor(chunk.cols / 2);
      console.log('[terrain DEBUG] center chunk:', {
        pos: `(${chunk.position.x.toFixed(1)}, ${chunk.position.z.toFixed(1)})`,
        bodyY: (chunk.position.y + offset.y).toFixed(2),
        heightsRange: `[${hMin.toFixed(2)}, ${hMax.toFixed(2)}]`,
        heightCenter: chunk.heights[mid].toFixed(2),
        scale: `(${chunk.scale.x.toFixed(1)}, ${chunk.scale.y}, ${chunk.scale.z.toFixed(1)})`,
        subdivs: `${subdivRows}x${subdivCols}`,
        cornerHeights: [
          chunk.heights[0].toFixed(2),
          chunk.heights[chunk.cols - 1].toFixed(2),
          chunk.heights[(chunk.rows - 1) * chunk.cols].toFixed(2),
          chunk.heights[chunk.rows * chunk.cols - 1].toFixed(2),
        ],
      });
    }

    return { body, collider };
  } catch (err) {
    const chunkKey = `${chunk.rows}x${chunk.cols}@(${chunk.position.x.toFixed(1)},${chunk.position.z.toFixed(1)})`;
    console.error(
      `[terrain] CRITICAL: Heightfield collider failed for chunk "%s", falling back to flat cuboid. Error: %s`,
      chunkKey,
      err
    );
    if (terrainEntity > 0) {
      TerrainDebugInfo.failedColliderChunks[terrainEntity]++;
    }
    try {
      const cx = chunk.position.x + offset.x;
      const cy = chunk.position.y + offset.y;
      const cz = chunk.position.z + offset.z;
      const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(cx, cy, cz);
      const body = physicsWorld.createRigidBody(bodyDesc);
      const halfExt = chunk.size * 0.5;
      const colliderDesc = RAPIER.ColliderDesc.cuboid(halfExt, 0.25, halfExt);
      colliderDesc.setFriction(0.8);
      colliderDesc.setRestitution(0.0);
      const collider = physicsWorld.createCollider(colliderDesc, body);
      return { body, collider };
    } catch (fallbackErr) {
      console.error('[terrain] Flat cuboid fallback also failed:', fallbackErr);
      return null;
    }
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

export function getTerrainHeightAt(
  state: State,
  worldX: number,
  worldZ: number
): number {
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (data.initialized) {
      const localX = worldX - data.worldOffset.x;
      const localZ = worldZ - data.worldOffset.z;
      return data.terrainLOD.getHeightAt(localX, localZ);
    }
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
  if (data?.initialized) {
    data.terrainLOD.setWireframe(enabled);
    data.lastWireframe = enabled ? 1 : 0;
  }
}

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
      const physicsCtx = getPhysicsContext(state);
      const physicsWorld = physicsCtx.physicsWorld;
      if (physicsWorld) cleanupPhysics(data, physicsWorld);
      data.collisionDispatchStarted = false;
      data.collisionReady = false;
      fireHeightmapReloadCallbacks(state);
    })
    .catch((err: unknown) => {
      console.error('[terrain] Failed to hot-reload heightmap:', err);
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

  const stats = data.terrainLOD.getStats();
  return {
    activeChunks: stats.instances.active,
    drawCalls: stats.drawCalls,
    totalInstances: stats.instances.total,
    geometries: stats.geometries,
    materials: stats.materials,
    failedColliderChunks: TerrainDebugInfo.failedColliderChunks[entity] ?? 0,
  };
}
