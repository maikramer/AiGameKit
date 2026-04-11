import * as THREE from 'three';
import { addComponent, defineQuery } from '../../core';
import type { State, System } from '../../core';
import {
  CollisionEvents,
  TouchedEvent,
  TouchEndedEvent,
} from '../physics/components';
import { getPhysicsContext, RAPIER } from '../physics';
import { PhysicsWorldSystem } from '../physics/systems';
import { PlayerController } from '../player/components';
import { getRenderingContext, MainCamera, threeCameras } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import { TransformHierarchySystem, WorldTransform } from '../transforms';
import {
  PlayerWaterState,
  SwimTriggerZone,
  Water,
  WaterSubmersionState,
} from './components';
import {
  createUnderwaterPostProcessMaterial,
  createWaterMaterial,
} from './water-material';
import { PlanarReflection } from './planar-reflection';
import {
  findNearestTerrainConfig,
  findNearestTerrainHeightmap,
  getWaterContext,
  type WaterEntityData,
} from './utils';
import { ScreenSpaceReflection } from '../postprocessing/components';

const waterQuery = defineQuery([Water]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);
const playerCollisionQuery = defineQuery([PlayerController, CollisionEvents]);
const swimTriggerZoneQuery = defineQuery([SwimTriggerZone]);
const ssrQuery = defineQuery([ScreenSpaceReflection]);

const REFLECTION_SIZE = 512;
const PLANE_SEGMENTS = 256;

const POST_PROCESS_RT = new WeakMap<State, THREE.WebGLRenderTarget>();
const POST_PROCESS_SCENE = new WeakMap<State, THREE.Scene>();
const POST_PROCESS_CAMERA = new WeakMap<State, THREE.OrthographicCamera>();
const POST_PROCESS_MATERIAL = new WeakMap<State, THREE.ShaderMaterial>();
const POST_PROCESS_QUAD = new WeakMap<State, THREE.Mesh>();

function ensurePostProcessResources(state: State): void {
  if (POST_PROCESS_RT.has(state)) return;

  const ctx = getRenderingContext(state);
  const renderer = ctx.renderer;
  if (!renderer) return;
  const size = new THREE.Vector2();
  renderer.getSize(size);

  const target = new THREE.WebGLRenderTarget(size.x, size.y, {
    depthBuffer: true,
    stencilBuffer: false,
  });
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const material = createUnderwaterPostProcessMaterial({
    color: new THREE.Color(0x0d4a61),
  });
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  scene.add(quad);

  POST_PROCESS_RT.set(state, target);
  POST_PROCESS_SCENE.set(state, scene);
  POST_PROCESS_CAMERA.set(state, camera);
  POST_PROCESS_MATERIAL.set(state, material);
  POST_PROCESS_QUAD.set(state, quad);
}

function disposePostProcessResources(state: State): void {
  const rt = POST_PROCESS_RT.get(state);
  const mat = POST_PROCESS_MATERIAL.get(state);
  const quad = POST_PROCESS_QUAD.get(state);

  if (quad) {
    const geometry = quad.geometry;
    geometry.dispose();
  }
  if (mat) mat.dispose();
  if (rt) rt.dispose();

  POST_PROCESS_RT.delete(state);
  POST_PROCESS_SCENE.delete(state);
  POST_PROCESS_CAMERA.delete(state);
  POST_PROCESS_MATERIAL.delete(state);
  POST_PROCESS_QUAD.delete(state);
}

function applyWaterSplash(
  context: Map<number, WaterEntityData>,
  waterEntity: number,
  position: THREE.Vector3,
  strength: number
): void {
  const waterData = context.get(waterEntity);
  if (!waterData) return;

  waterData.rippleCenter.copy(position);
  waterData.rippleCenter.y = waterData.worldOffset.y;
  waterData.rippleStrength = Math.max(waterData.rippleStrength, strength);
}

export const WaterBootstrapSystem: System = {
  group: 'fixed',
  after: [PhysicsWorldSystem],
  update(state: State) {
    if (state.headless) return;

    const ctx = getRenderingContext(state);
    const scene = ctx.scene;
    const context = getWaterContext(state);
    const entities = waterQuery(state.world);

    for (const entity of entities) {
      if (context.has(entity)) continue;

      const size = Water.size[entity];
      const waterLevel = Water.waterLevel[entity];
      const opacity = Water.opacity[entity];
      const tint = new THREE.Color(
        Water.tintR[entity],
        Water.tintG[entity],
        Water.tintB[entity]
      );
      const waveSpeed = Water.waveSpeed[entity];
      const waveScale = Water.waveScale[entity];
      const wireframe = Water.wireframe[entity] === 1;

      const terrainConfig = findNearestTerrainConfig(state);
      const terrainWorldSize = terrainConfig?.worldSize ?? size;
      const terrainMaxHeight = terrainConfig?.maxHeight ?? 50;

      const reflection = new PlanarReflection(REFLECTION_SIZE, REFLECTION_SIZE);
      const heightmap = findNearestTerrainHeightmap(state);

      const geometry = new THREE.PlaneGeometry(
        size,
        size,
        PLANE_SEGMENTS,
        PLANE_SEGMENTS
      );
      geometry.rotateX(-Math.PI / 2);

      const material = createWaterMaterial({
        waterLevel,
        opacity,
        tint,
        waveSpeed,
        waveScale,
        wireframe,
        terrainWorldSize,
        terrainMaxHeight,
        underwaterFogColor: new THREE.Color(0x001a33),
        underwaterFogDensity: 0.04,
        reflectionTexture: reflection.texture,
        heightmapTexture: heightmap,
      });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.userData._isWater = true;
      mesh.renderOrder = 999;

      if (state.hasComponent(entity, WorldTransform)) {
        mesh.position.set(
          WorldTransform.posX[entity],
          WorldTransform.posY[entity],
          WorldTransform.posZ[entity]
        );
      }

      const data: WaterEntityData = {
        mesh,
        material,
        reflection,
        initialized: true,
        worldOffset: { x: 0, y: waterLevel, z: 0 },
        physicsBody: null,
        physicsCollider: null,
        isSubmerged: false,
        rippleCenter: new THREE.Vector3(0, waterLevel, 0),
        rippleStrength: 0,
        rippleDecay: 0.92,
        underwaterPostProcessActive: false,
        audioMuffleHint: false,
      };

      if (state.hasComponent(entity, WorldTransform)) {
        data.worldOffset = {
          x: WorldTransform.posX[entity],
          y: WorldTransform.posY[entity],
          z: WorldTransform.posZ[entity],
        };
      }

      context.set(entity, data);
      scene.add(mesh);
    }

    const physicsWorld = getPhysicsContext(state).physicsWorld;
    for (const [entity, data] of context) {
      if (!state.exists(entity)) {
        if (physicsWorld) cleanupPhysics(data, physicsWorld);
        scene.remove(data.mesh);
        data.mesh.geometry.dispose();
        data.material.dispose();
        data.reflection.dispose();
        context.delete(entity);
      }
    }
  },
  dispose(state: State) {
    const ctx = getRenderingContext(state);
    const scene = ctx?.scene;
    const physicsWorld = getPhysicsContext(state).physicsWorld;
    const context = getWaterContext(state);

    for (const [, data] of context) {
      if (physicsWorld) cleanupPhysics(data, physicsWorld);
      if (scene) scene.remove(data.mesh);
      data.mesh.geometry.dispose();
      data.material.dispose();
      data.reflection.dispose();
    }
    context.clear();

    disposePostProcessResources(state);
  },
};

export const WaterInteractionSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state: State) {
    const context = getWaterContext(state);
    const players = playerCollisionQuery(state.world);
    const now = state.time.elapsed;

    for (const entity of players) {
      if (!state.hasComponent(entity, PlayerWaterState)) {
        addComponent(state.world, PlayerWaterState, entity);
        PlayerWaterState.state[entity] = WaterSubmersionState.Outside;
        PlayerWaterState.waterEntity[entity] = 0;
        PlayerWaterState.entryTime[entity] = 0;
        PlayerWaterState.submersionDepth[entity] = 0;
        PlayerWaterState.swimTriggered[entity] = 0;
        PlayerWaterState.swimZoneEntity[entity] = 0;
      }

      const touched = state.hasComponent(entity, TouchedEvent);
      const ended = state.hasComponent(entity, TouchEndedEvent);

      if (touched) {
        const other = TouchedEvent.other[entity];
        if (state.hasComponent(other, Water)) {
          const waterEntity = other;
          const waterLevel = Water.waterLevel[waterEntity];
          const posY = state.hasComponent(entity, WorldTransform)
            ? WorldTransform.posY[entity]
            : waterLevel;

          PlayerWaterState.state[entity] = WaterSubmersionState.Entering;
          PlayerWaterState.waterEntity[entity] = waterEntity;
          PlayerWaterState.entryTime[entity] = now;
          PlayerWaterState.submersionDepth[entity] = Math.max(
            0,
            waterLevel - posY
          );
          PlayerWaterState.swimTriggered[entity] = 0;
          PlayerWaterState.swimZoneEntity[entity] = 0;

          const splashPos = new THREE.Vector3(
            state.hasComponent(entity, WorldTransform)
              ? WorldTransform.posX[entity]
              : 0,
            waterLevel,
            state.hasComponent(entity, WorldTransform)
              ? WorldTransform.posZ[entity]
              : 0
          );
          applyWaterSplash(context, waterEntity, splashPos, 0.85);
        }
      }

      if (ended) {
        const other = TouchEndedEvent.other[entity];
        if (state.hasComponent(other, Water)) {
          const waterEntity = other;
          const waterLevel = Water.waterLevel[waterEntity];

          PlayerWaterState.state[entity] = WaterSubmersionState.Exiting;
          PlayerWaterState.submersionDepth[entity] = 0;

          const splashPos = new THREE.Vector3(
            state.hasComponent(entity, WorldTransform)
              ? WorldTransform.posX[entity]
              : 0,
            waterLevel,
            state.hasComponent(entity, WorldTransform)
              ? WorldTransform.posZ[entity]
              : 0
          );
          applyWaterSplash(context, waterEntity, splashPos, 0.45);

          PlayerWaterState.waterEntity[entity] = 0;
          PlayerWaterState.swimTriggered[entity] = 0;
          PlayerWaterState.swimZoneEntity[entity] = 0;
          PlayerWaterState.state[entity] = WaterSubmersionState.Outside;
        }
      }

      const activeWater = PlayerWaterState.waterEntity[entity];
      if (activeWater !== 0 && state.hasComponent(activeWater, Water)) {
        const waterLevel = Water.waterLevel[activeWater];
        const py = state.hasComponent(entity, WorldTransform)
          ? WorldTransform.posY[entity]
          : waterLevel;

        PlayerWaterState.submersionDepth[entity] = Math.max(0, waterLevel - py);
        if (PlayerWaterState.submersionDepth[entity] > 0.05) {
          PlayerWaterState.state[entity] = WaterSubmersionState.Submerged;
        }

        const ripplePos = new THREE.Vector3(
          state.hasComponent(entity, WorldTransform)
            ? WorldTransform.posX[entity]
            : 0,
          waterLevel,
          state.hasComponent(entity, WorldTransform)
            ? WorldTransform.posZ[entity]
            : 0
        );
        applyWaterSplash(context, activeWater, ripplePos, 0.2);
      }
    }

    for (const [, data] of context) {
      data.rippleStrength *= data.rippleDecay;
      if (data.rippleStrength < 0.001) {
        data.rippleStrength = 0;
      }
    }
  },
};

export const SwimTriggerSystem: System = {
  group: 'simulation',
  after: [WaterInteractionSystem],
  update(state: State) {
    const players = playerCollisionQuery(state.world);
    const zones = swimTriggerZoneQuery(state.world);

    for (const player of players) {
      if (!state.hasComponent(player, PlayerWaterState)) continue;
      const currentWater = PlayerWaterState.waterEntity[player];
      if (currentWater === 0) continue;

      for (const zone of zones) {
        if (SwimTriggerZone.enabled[zone] !== 1) continue;
        if (SwimTriggerZone.waterEntity[zone] !== currentWater) continue;

        PlayerWaterState.swimTriggered[player] = 1;
        PlayerWaterState.swimZoneEntity[player] = zone;
        break;
      }
    }
  },
};

export const WaterRenderSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;

    const ctx = getRenderingContext(state);
    const renderer = ctx.renderer;
    const scene = ctx.scene;
    if (!renderer) return;

    const context = getWaterContext(state);
    const cameraEntities = cameraQuery(state.world);
    if (cameraEntities.length === 0) return;

    const camera = threeCameras.get(cameraEntities[0]);
    if (!camera || !(camera instanceof THREE.PerspectiveCamera)) return;

    const time = state.time.elapsed;
    let strongestUnderwaterFade = 0;
    let waterLineY = 0.5;

    for (const entity of waterQuery(state.world)) {
      const data = context.get(entity);
      if (!data) continue;

      if (state.hasComponent(entity, WorldTransform)) {
        const ox = WorldTransform.posX[entity];
        const oy = WorldTransform.posY[entity];
        const oz = WorldTransform.posZ[entity];
        data.mesh.position.set(ox, oy, oz);
        data.worldOffset = { x: ox, y: oy, z: oz };
      }

      data.material.uniforms.uTime.value = time;
      data.material.uniforms.uCameraPosition.value.copy(camera.position);
      data.material.uniforms.uRippleCenter.value.copy(data.rippleCenter);
      data.material.uniforms.uRippleStrength.value = data.rippleStrength;

      const waterLevel = Water.waterLevel[entity];
      const cameraY = camera.position.y;
      const underwaterFade =
        cameraY < waterLevel
          ? Math.min(1.0, (waterLevel - cameraY) / 5.0)
          : 0.0;
      if (data.material.uniforms.uUnderwaterFade) {
        data.material.uniforms.uUnderwaterFade.value = underwaterFade;
      }

      data.underwaterPostProcessActive = underwaterFade > 0;
      data.audioMuffleHint = underwaterFade > 0.1;

      if (underwaterFade > strongestUnderwaterFade) {
        strongestUnderwaterFade = underwaterFade;
        const projected = new THREE.Vector3(0, waterLevel, 0).project(camera);
        waterLineY = projected.y * 0.5 + 0.5;
      }

      const fogR = Water.underwaterFogColorR[entity] ?? 0.0;
      const fogG = Water.underwaterFogColorG[entity] ?? 0.0;
      const fogB = Water.underwaterFogColorB[entity] ?? 0.0;
      const fogDensity = Water.underwaterFogDensity[entity] ?? 0.0;
      if (data.material.uniforms.uUnderwaterFogColor) {
        data.material.uniforms.uUnderwaterFogColor.value.setRGB(
          fogR,
          fogG,
          fogB
        );
      }
      if (data.material.uniforms.uUnderwaterFogDensity) {
        data.material.uniforms.uUnderwaterFogDensity.value = fogDensity;
      }

      const heightmap = findNearestTerrainHeightmap(state);
      if (heightmap && !data.material.uniforms.tHeightMap.value) {
        data.material.uniforms.tHeightMap.value = heightmap;
        data.material.uniforms.uHasHeightmap.value = 1.0;
      }

      const hasSSR = ssrQuery(state.world).length > 0;
      if (!hasSSR && underwaterFade === 0) {
        data.reflection.render(renderer, scene, camera, data.worldOffset.y);
      }
    }

    if (strongestUnderwaterFade > 0.001) {
      ensurePostProcessResources(state);
      const rt = POST_PROCESS_RT.get(state);
      const postScene = POST_PROCESS_SCENE.get(state);
      const postCamera = POST_PROCESS_CAMERA.get(state);
      const postMaterial = POST_PROCESS_MATERIAL.get(state);

      if (!rt || !postScene || !postCamera || !postMaterial) return;

      const size = new THREE.Vector2();
      renderer.getSize(size);
      if (rt.width !== size.x || rt.height !== size.y) {
        rt.setSize(size.x, size.y);
      }

      renderer.setRenderTarget(rt);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);

      postMaterial.uniforms.uSceneTexture.value = rt.texture;
      postMaterial.uniforms.uTime.value = time;
      postMaterial.uniforms.uUnderwaterFade.value = strongestUnderwaterFade;
      postMaterial.uniforms.uLineY.value = THREE.MathUtils.clamp(
        waterLineY,
        0,
        1
      );

      renderer.render(postScene, postCamera);
    }
  },
};

export const WaterPhysicsSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state: State) {
    const physicsCtx = getPhysicsContext(state);
    const physicsWorld = physicsCtx.physicsWorld;
    if (!physicsWorld) return;

    const context = getWaterContext(state);
    const entities = waterQuery(state.world);

    for (const entity of entities) {
      const data = context.get(entity);
      if (!data || data.physicsBody) continue;

      const size = Water.size[entity];
      let ox = 0;
      let oy = Water.waterLevel[entity];
      let oz = 0;
      if (state.hasComponent(entity, WorldTransform)) {
        ox = WorldTransform.posX[entity];
        oy = WorldTransform.posY[entity];
        oz = WorldTransform.posZ[entity];
      }

      const bodyDesc = RAPIER.RigidBodyDesc.fixed().setTranslation(ox, oy, oz);
      const body = physicsWorld.createRigidBody(bodyDesc);

      const halfExtents = new RAPIER.Vector3(size / 2, 2.0, size / 2);
      const colliderDesc = RAPIER.ColliderDesc.cuboid(
        halfExtents.x,
        halfExtents.y,
        halfExtents.z
      );
      colliderDesc.setSensor(true);

      const collider = physicsWorld.createCollider(colliderDesc, body);

      data.physicsBody = body;
      data.physicsCollider = collider;
    }
  },
  dispose(state: State) {
    const physicsCtx = getPhysicsContext(state);
    const physicsWorld = physicsCtx.physicsWorld;
    if (!physicsWorld) return;

    const context = getWaterContext(state);
    for (const [, data] of context) {
      cleanupPhysics(data, physicsWorld);
    }
  },
};

function cleanupPhysics(
  data: WaterEntityData,
  physicsWorld: RAPIER.World
): void {
  if (data.physicsCollider) {
    physicsWorld.removeCollider(data.physicsCollider, false);
    data.physicsCollider = null;
  }
  if (data.physicsBody) {
    physicsWorld.removeRigidBody(data.physicsBody);
    data.physicsBody = null;
  }
}

export function isUnderwaterAudioMuffleActive(state: State): boolean {
  const context = getWaterContext(state);
  for (const [, data] of context) {
    if (data.audioMuffleHint) return true;
  }
  return false;
}
