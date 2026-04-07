import * as THREE from 'three';
import type { State, System } from '../../core';
import { defineQuery } from '../../core';
import { getPhysicsContext, RAPIER } from '../physics';
import { PhysicsWorldSystem } from '../physics/systems';
import { getRenderingContext, MainCamera, threeCameras } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import { TransformHierarchySystem, WorldTransform } from '../transforms';
import { Water } from './components';
import { createWaterMaterial } from './water-material';
import { PlanarReflection } from './planar-reflection';
import {
  findNearestTerrainConfig,
  findNearestTerrainHeightmap,
  getWaterContext,
  type WaterEntityData,
} from './utils';

const waterQuery = defineQuery([Water]);
const cameraQuery = defineQuery([MainCamera, WorldTransform]);

const REFLECTION_SIZE = 512;
const PLANE_SEGMENTS = 256;

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
      // Underwater state: compute fade based on camera height relative to water level
      const waterLevel = Water.waterLevel[entity];
      const cameraY = camera.position.y;
      const underwaterFade = cameraY < waterLevel
        ? Math.min(1.0, (waterLevel - cameraY) / 5.0)
        : 0.0;
      if (data.material.uniforms.uUnderwaterFade) {
        data.material.uniforms.uUnderwaterFade.value = underwaterFade;
      }
      // Underwater fog color and density from component
      const fogR = Water.underwaterFogColorR[entity] ?? 0.0;
      const fogG = Water.underwaterFogColorG[entity] ?? 0.0;
      const fogB = Water.underwaterFogColorB[entity] ?? 0.0;
      const fogDensity = Water.underwaterFogDensity[entity] ?? 0.0;
      if (data.material.uniforms.uUnderwaterFogColor) {
        data.material.uniforms.uUnderwaterFogColor.value.setRGB(fogR, fogG, fogB);
      }
      if (data.material.uniforms.uUnderwaterFogDensity) {
        data.material.uniforms.uUnderwaterFogDensity.value = fogDensity;
      }

      const heightmap = findNearestTerrainHeightmap(state);
      if (heightmap && !data.material.uniforms.tHeightMap.value) {
        data.material.uniforms.tHeightMap.value = heightmap;
        data.material.uniforms.uHasHeightmap.value = 1.0;
      }

      data.reflection.render(renderer, scene, camera, data.worldOffset.y);
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
