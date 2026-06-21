import { logger } from '../../core/utils/logger';
import * as THREE from 'three';
import type { State } from '../../core';
import { defineQuery, type System } from '../../core';
import { WorldTransform } from '../transforms';
import { ThirdPersonCamera } from '../player-controller/components';
import {
  AmbientLight,
  DirectionalLight,
  DistanceCull,
  MainCamera,
  PointLight,
  RenderContext,
  MeshRenderer,
  SpotLight,
} from './components';
import { getOrCreateMesh, hideInstance, updateInstance } from './operations';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import {
  applyNeutralEnvironment,
  createRenderer,
  createThreeCamera,
  deleteCanvasElement,
  getCanvasElement,
  getRenderingContext,
  getScene,
  handleWindowResize,
  SHADOW_CONFIG,
  syncCameraSettings,
  threeCameras,
} from './utils';

const rendererQuery = defineQuery([MeshRenderer]);
const distanceCullQuery = defineQuery([DistanceCull, WorldTransform]);
const ambientQuery = defineQuery([AmbientLight]);
const directionalQuery = defineQuery([DirectionalLight]);
const thirdPersonCameraQuery = defineQuery([ThirdPersonCamera]);
const mainCameraTransformQuery = defineQuery([MainCamera, WorldTransform]);
const mainCameraQuery = defineQuery([MainCamera]);
const renderContextQuery = defineQuery([RenderContext]);
const _lightDir = new THREE.Vector3();
const _lightOffset = new THREE.Vector3();
const _lightPos = new THREE.Vector3();
const _shadowCenter = new THREE.Vector3();
const _lightPosition = new THREE.Vector3();
const _lightQuaternion = new THREE.Quaternion();
const _lightForward = new THREE.Vector3(0, 0, -1);

const pointLightQuery = defineQuery([PointLight, WorldTransform]);
const spotLightQuery = defineQuery([SpotLight, WorldTransform]);
const entityToPointLightByState = new WeakMap<
  State,
  Map<number, THREE.PointLight>
>();
const entityToSpotLightByState = new WeakMap<
  State,
  Map<number, THREE.SpotLight>
>();
const entityToDirectionalLightByState = new WeakMap<
  State,
  Map<number, THREE.DirectionalLight>
>();
const entityToAmbientLightByState = new WeakMap<
  State,
  Map<number, THREE.HemisphereLight>
>();

function getPointLightMap(state: State): Map<number, THREE.PointLight> {
  let map = entityToPointLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToPointLightByState.set(state, map);
  }
  return map;
}

function getSpotLightMap(state: State): Map<number, THREE.SpotLight> {
  let map = entityToSpotLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToSpotLightByState.set(state, map);
  }
  return map;
}

function getDirectionalLightMap(
  state: State
): Map<number, THREE.DirectionalLight> {
  let map = entityToDirectionalLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToDirectionalLightByState.set(state, map);
  }
  return map;
}

function getAmbientLightMap(state: State): Map<number, THREE.HemisphereLight> {
  let map = entityToAmbientLightByState.get(state);
  if (!map) {
    map = new Map();
    entityToAmbientLightByState.set(state, map);
  }
  return map;
}

// Last-applied light/shadow values keyed by the Three.js light object. The
// sync systems compare the current ECS values against these and only write to
// the light/uniform when something changed, so static lights cost ~0 per
// frame instead of rewriting uniforms and rebuilding the shadow projection
// matrix every tick. Mirrors the dirty-gating used in operations.ts for
// instanced meshes. NaN sentinels force a first-frame apply.
interface AmbientLightCache {
  skyColor: number;
  groundColor: number;
  intensity: number;
}
interface DirectionalLightCache {
  color: number;
  intensity: number;
  mapSize: number;
  bias: number;
  normalBias: number;
  frustumLeft: number;
  frustumRight: number;
  frustumTop: number;
  frustumBottom: number;
  frustumNear: number;
  frustumFar: number;
}
interface PointLightCache {
  color: number;
  intensity: number;
  distance: number;
  decay: number;
}
interface SpotLightCache {
  color: number;
  intensity: number;
  distance: number;
  decay: number;
  angle: number;
  penumbra: number;
}
const ambientLightCache = new WeakMap<
  THREE.HemisphereLight,
  AmbientLightCache
>();
const directionalLightCache = new WeakMap<
  THREE.DirectionalLight,
  DirectionalLightCache
>();
const pointLightCache = new WeakMap<THREE.PointLight, PointLightCache>();
const spotLightCache = new WeakMap<THREE.SpotLight, SpotLightCache>();

// Soft budget guard, not a shader-uniform limit (three recompiles per light
// count). Point lights here don't cast shadows, so they're cheap; 12 comfortably
// covers a lit village (torches, hearths, beacons) on desktop GPUs.
const MAX_POINT_LIGHTS = 12;
const MAX_SPOT_LIGHTS = 2;

function resolveShadowCenter(state: State): THREE.Vector3 {
  _shadowCenter.copy(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER);

  const thirdPersonCams = thirdPersonCameraQuery(state.world);
  if (thirdPersonCams.length > 0) {
    const targetEid = ThirdPersonCamera.target[thirdPersonCams[0]];
    if (targetEid > 0 && state.hasComponent(targetEid, WorldTransform)) {
      _shadowCenter.set(
        WorldTransform.posX[targetEid],
        WorldTransform.posY[targetEid],
        WorldTransform.posZ[targetEid]
      );
    }
  }

  return _shadowCenter;
}

/** Dispose every geometry/material/texture reachable from `root`, dedup-guarded. */
function disposeSceneGraph(root: THREE.Object3D): void {
  const disposedGeometries = new Set<THREE.BufferGeometry>();
  const disposedMaterials = new Set<THREE.Material>();
  const disposedTextures = new Set<THREE.Texture>();
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const geos = Array.isArray(mesh.geometry) ? mesh.geometry : [mesh.geometry];
    for (const g of geos) {
      if (g && !disposedGeometries.has(g)) {
        try {
          g.dispose();
        } catch {
          /* one failed dispose must not block the rest */
        }
        disposedGeometries.add(g);
      }
    }
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      if (!m || disposedMaterials.has(m)) continue;
      disposedMaterials.add(m);
      for (const k in m) {
        const v = (m as unknown as Record<string, unknown>)[k];
        if (v && typeof v === 'object' && 'isTexture' in v) {
          const tex = v as THREE.Texture;
          if (!disposedTextures.has(tex)) {
            try {
              tex.dispose();
            } catch {
              /* ignore */
            }
            disposedTextures.add(tex);
          }
        }
      }
      try {
        m.dispose();
      } catch {
        /* ignore */
      }
    }
  });
}

export const MeshInstanceSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);

    for (const [entity, instanceInfo] of context.entityInstances) {
      if (!state.exists(entity)) {
        const pools = instanceInfo.unlit
          ? context.unlitMeshPools
          : context.meshPools;
        const mesh = pools.get(instanceInfo.poolId);
        if (mesh) {
          hideInstance(mesh, entity, context);
        }
        context.entityInstances.delete(entity);
        context.totalInstanceCount--;
      }
    }

    const rendererEntities = rendererQuery(state.world);
    for (const entity of rendererEntities) {
      const unlit = MeshRenderer.unlit[entity] === 1;
      let mesh = getOrCreateMesh(context, MeshRenderer.shape[entity], unlit);
      if (!mesh) continue;

      if (MeshRenderer.visible[entity] !== 1) {
        hideInstance(mesh, entity, context);
        continue;
      }

      mesh = updateInstance(mesh, entity, context, state, unlit);
    }
  },
};

export const DistanceCullSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;

    const camEntities = mainCameraQuery(state.world);
    if (camEntities.length === 0) return;
    const camera = threeCameras.get(camEntities[0]);
    if (!camera) return;

    const camX = camera.position.x;
    const camZ = camera.position.z;

    const HYSTERESIS = 0.9;

    for (const eid of distanceCullQuery(state.world)) {
      const maxDist = DistanceCull.maxDistance[eid];
      if (maxDist <= 0) continue;

      const dx = WorldTransform.posX[eid] - camX;
      const dz = WorldTransform.posZ[eid] - camZ;
      const dist = Math.sqrt(dx * dx + dz * dz);

      const wasCulled = DistanceCull.culled[eid] === 1;
      const shouldCull = wasCulled
        ? dist >= maxDist * HYSTERESIS
        : dist > maxDist;

      if (shouldCull === wasCulled) continue;

      DistanceCull.culled[eid] = shouldCull ? 1 : 0;

      const gltfGroup = getGltfRootGroup(state, eid);
      if (gltfGroup) {
        gltfGroup.visible = !shouldCull;
      }

      if (state.hasComponent(eid, MeshRenderer)) {
        MeshRenderer.visible[eid] = shouldCull ? 0 : 1;
      }
    }
  },
};

export const LightSyncSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    const entityToAmbientLight = getAmbientLightMap(state);
    const entityToDirectionalLight = getDirectionalLightMap(state);

    // --- Ambient lights (per-entity, Map-based) ---
    for (const [eid, light] of entityToAmbientLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        light.dispose();
        entityToAmbientLight.delete(eid);
      }
    }

    const ambients = ambientQuery(state.world);
    for (const entity of ambients) {
      let light = entityToAmbientLight.get(entity);
      if (!light) {
        // Adopt the bootstrap hemisphere light (already added to the scene in
        // initializeContext) for the first ambient entity so it is actually
        // synced rather than left orphaned; extra ambient entities get fresh
        // lights.
        const boot = getRenderingContext(state).lights.ambient;
        if (boot && ![...entityToAmbientLight.values()].includes(boot)) {
          light = boot;
        } else {
          light = new THREE.HemisphereLight();
          scene.add(light);
        }
        entityToAmbientLight.set(entity, light);
      }

      const sky = AmbientLight.skyColor[entity];
      const ground = AmbientLight.groundColor[entity];
      const intensity = AmbientLight.intensity[entity];
      let cache = ambientLightCache.get(light);
      if (cache === undefined) {
        cache = { skyColor: NaN, groundColor: NaN, intensity: NaN };
        ambientLightCache.set(light, cache);
      }
      if (cache.skyColor !== sky) {
        light.color.setHex(sky);
        cache.skyColor = sky;
      }
      if (cache.groundColor !== ground) {
        light.groundColor.setHex(ground);
        cache.groundColor = ground;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
    }

    // --- Directional lights (per-entity, Map-based) ---
    for (const [eid, light] of entityToDirectionalLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        if (light.target) scene.remove(light.target);
        light.dispose();
        entityToDirectionalLight.delete(eid);
      }
    }

    const directionals = directionalQuery(state.world);
    for (const entity of directionals) {
      let light = entityToDirectionalLight.get(entity);
      if (!light) {
        // Adopt the bootstrap directional light (already in the scene with its
        // target) for the first directional entity so it is positioned/synced
        // instead of left orphaned; extra directional entities get fresh lights.
        const boot = getRenderingContext(state).lights.directional;
        if (boot && ![...entityToDirectionalLight.values()].includes(boot)) {
          light = boot;
        } else {
          light = new THREE.DirectionalLight();
          scene.add(light);
          scene.add(light.target);
        }
        light.castShadow = true;
        entityToDirectionalLight.set(entity, light);
      }

      const color = DirectionalLight.color[entity];
      const intensity = DirectionalLight.intensity[entity];
      let cache = directionalLightCache.get(light);
      if (cache === undefined) {
        cache = {
          color: NaN,
          intensity: NaN,
          mapSize: NaN,
          bias: NaN,
          normalBias: NaN,
          frustumLeft: NaN,
          frustumRight: NaN,
          frustumTop: NaN,
          frustumBottom: NaN,
          frustumNear: NaN,
          frustumFar: NaN,
        };
        directionalLightCache.set(light, cache);
      }

      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }

      _lightDir
        .set(
          DirectionalLight.directionX[entity],
          DirectionalLight.directionY[entity],
          DirectionalLight.directionZ[entity]
        )
        .normalize();

      if (DirectionalLight.castShadow[entity] === 1) {
        light.castShadow = true;

        const mapSize = DirectionalLight.shadowMapSize[entity];
        const bias = -0.0001;
        const normalBias = 0.02;
        const radius = SHADOW_CONFIG.CAMERA_RADIUS;
        const near = SHADOW_CONFIG.NEAR_PLANE;
        const far = SHADOW_CONFIG.FAR_PLANE;

        // Static shadow config: apply + rebuild projection only when a value
        // changed, not every frame.
        let shadowChanged = false;
        if (cache.mapSize !== mapSize) {
          light.shadow.mapSize.width = mapSize;
          light.shadow.mapSize.height = mapSize;
          cache.mapSize = mapSize;
          shadowChanged = true;
        }
        if (cache.bias !== bias) {
          light.shadow.bias = bias;
          cache.bias = bias;
          shadowChanged = true;
        }
        if (cache.normalBias !== normalBias) {
          light.shadow.normalBias = normalBias;
          cache.normalBias = normalBias;
          shadowChanged = true;
        }
        const shadowCamera = light.shadow.camera as THREE.OrthographicCamera;
        if (
          cache.frustumLeft !== -radius ||
          cache.frustumRight !== radius ||
          cache.frustumTop !== radius ||
          cache.frustumBottom !== -radius ||
          cache.frustumNear !== near ||
          cache.frustumFar !== far
        ) {
          shadowCamera.left = -radius;
          shadowCamera.right = radius;
          shadowCamera.top = radius;
          shadowCamera.bottom = -radius;
          shadowCamera.near = near;
          shadowCamera.far = far;
          cache.frustumLeft = -radius;
          cache.frustumRight = radius;
          cache.frustumTop = radius;
          cache.frustumBottom = -radius;
          cache.frustumNear = near;
          cache.frustumFar = far;
          shadowChanged = true;
        }
        if (shadowChanged) shadowCamera.updateProjectionMatrix();

        // Shadow frustum follows the player — keep this tracking per-frame.
        const shadowCenter = resolveShadowCenter(state);
        _lightPos
          .copy(shadowCenter)
          .add(
            _lightOffset
              .copy(_lightDir)
              .multiplyScalar(DirectionalLight.distance[entity])
          );

        light.position.copy(_lightPos);
        light.target.position.copy(shadowCenter);
        light.target.updateMatrixWorld();
        shadowCamera.position.copy(_lightPos);
        shadowCamera.lookAt(shadowCenter);
        shadowCamera.updateMatrixWorld();
      } else {
        light.castShadow = false;
      }
    }
  },
};

export const PointSpotLightSyncSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    const scene = getScene(state);
    if (!scene) return;

    const entityToPointLight = getPointLightMap(state);
    const entityToSpotLight = getSpotLightMap(state);

    for (const [eid, light] of entityToPointLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        light.dispose();
        entityToPointLight.delete(eid);
        const idx = context.lights.pointLights.indexOf(light);
        if (idx !== -1) context.lights.pointLights.splice(idx, 1);
      }
    }

    for (const [eid, light] of entityToSpotLight) {
      if (!state.exists(eid)) {
        scene.remove(light);
        if (light.target) scene.remove(light.target);
        light.dispose();
        entityToSpotLight.delete(eid);
        const idx = context.lights.spotLights.indexOf(light);
        if (idx !== -1) context.lights.spotLights.splice(idx, 1);
      }
    }

    const pointEntities = pointLightQuery(state.world);
    for (const eid of pointEntities) {
      let light = entityToPointLight.get(eid);
      if (!light) {
        if (context.lights.pointLights.length >= MAX_POINT_LIGHTS) {
          logger.warn(
            `PointLight limit (${MAX_POINT_LIGHTS}) reached — skipping entity ${eid}`
          );
          continue;
        }
        light = new THREE.PointLight();
        scene.add(light);
        entityToPointLight.set(eid, light);
        context.lights.pointLights.push(light);
      }

      const color = PointLight.color[eid];
      const intensity = PointLight.intensity[eid];
      const distance = PointLight.distance[eid];
      const decay = PointLight.decay[eid];
      let cache = pointLightCache.get(light);
      if (cache === undefined) {
        cache = { color: NaN, intensity: NaN, distance: NaN, decay: NaN };
        pointLightCache.set(light, cache);
      }
      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
      if (cache.distance !== distance) {
        light.distance = distance;
        cache.distance = distance;
      }
      if (cache.decay !== decay) {
        light.decay = decay;
        cache.decay = decay;
      }

      _lightPosition.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid]
      );
      light.position.copy(_lightPosition);

      _lightQuaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
      light.quaternion.copy(_lightQuaternion);
    }

    const spotEntities = spotLightQuery(state.world);
    for (const eid of spotEntities) {
      let light = entityToSpotLight.get(eid);
      if (!light) {
        if (context.lights.spotLights.length >= MAX_SPOT_LIGHTS) {
          logger.warn(
            `SpotLight limit (${MAX_SPOT_LIGHTS}) reached — skipping entity ${eid}`
          );
          continue;
        }
        light = new THREE.SpotLight();
        scene.add(light);
        scene.add(light.target);
        entityToSpotLight.set(eid, light);
        context.lights.spotLights.push(light);
      }

      const color = SpotLight.color[eid];
      const intensity = SpotLight.intensity[eid];
      const distance = SpotLight.distance[eid];
      const decay = SpotLight.decay[eid];
      const angle = SpotLight.angle[eid];
      const penumbra = SpotLight.penumbra[eid];
      let cache = spotLightCache.get(light);
      if (cache === undefined) {
        cache = {
          color: NaN,
          intensity: NaN,
          distance: NaN,
          decay: NaN,
          angle: NaN,
          penumbra: NaN,
        };
        spotLightCache.set(light, cache);
      }
      if (cache.color !== color) {
        light.color.setHex(color);
        cache.color = color;
      }
      if (cache.intensity !== intensity) {
        light.intensity = intensity;
        cache.intensity = intensity;
      }
      if (cache.distance !== distance) {
        light.distance = distance;
        cache.distance = distance;
      }
      if (cache.decay !== decay) {
        light.decay = decay;
        cache.decay = decay;
      }
      if (cache.angle !== angle) {
        light.angle = angle;
        cache.angle = angle;
      }
      if (cache.penumbra !== penumbra) {
        light.penumbra = penumbra;
        cache.penumbra = penumbra;
      }

      _lightPosition.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid]
      );
      light.position.copy(_lightPosition);

      _lightQuaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
      light.quaternion.copy(_lightQuaternion);
      light.target.position.copy(_lightPosition);
      light.target.quaternion.copy(_lightQuaternion);
      _lightForward.set(0, 0, -1).applyQuaternion(_lightQuaternion);
      light.target.position.copy(_lightPosition).add(_lightForward);
    }
  },
};

// NOTE: RendererSetupSystem was removed — its logic was identical to
// SceneRenderSystem and caused duplicate resize listeners / double-setup.
// All renderer creation is now handled by SceneRenderSystem.

export const CameraSyncSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const cameraEntities = mainCameraTransformQuery(state.world);

    for (const entity of cameraEntities) {
      let camera = threeCameras.get(entity);
      if (!camera) {
        camera = createThreeCamera(
          entity,
          state,
          MainCamera.projection[entity],
          MainCamera.fov[entity],
          MainCamera.orthoSize[entity]
        );
      }

      camera.position.set(
        WorldTransform.posX[entity],
        WorldTransform.posY[entity],
        WorldTransform.posZ[entity]
      );

      camera.quaternion.set(
        WorldTransform.rotX[entity],
        WorldTransform.rotY[entity],
        WorldTransform.rotZ[entity],
        WorldTransform.rotW[entity]
      );

      syncCameraSettings(camera, entity, state);
    }
  },
};

export const SceneRenderSystem: System = {
  group: 'draw',
  last: true,
  async setup(state: State) {
    if (state.headless) return;
    const contextEntities = renderContextQuery(state.world);
    if (contextEntities.length === 0) return;

    const context = getRenderingContext(state);
    if (context.renderer) return;

    const entity = contextEntities[0];
    const canvas = getCanvasElement(entity);
    if (!canvas) return;

    const clearColor = RenderContext.clearColor[entity];
    const renderer = await createRenderer(canvas, clearColor);

    context.renderer = renderer;
    context.canvas = canvas;
    applyNeutralEnvironment(renderer, context.scene);
    // The post-processing scene pass renders scene.background (not the renderer
    // clear colour), so mirror the clear colour there or the sky goes black.
    if (clearColor !== 0)
      context.scene.background = new THREE.Color(clearColor);

    const onResize = () => handleWindowResize(state, renderer);
    context.resizeHandler = onResize;
    window.addEventListener('resize', onResize);
  },
  update(state: State) {
    if (state.headless) return;
  },
  dispose(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.resizeHandler) {
      window.removeEventListener('resize', context.resizeHandler);
      context.resizeHandler = undefined;
    }
    if (context.renderer) {
      context.renderer.setAnimationLoop(null);
      context.renderer.dispose();
      context.renderer = undefined;
      context.canvas = undefined;
    }

    // Dispose entity-level lights still held by the per-entity maps.
    const entityToPointLight = getPointLightMap(state);
    const entityToSpotLight = getSpotLightMap(state);
    const entityToDirectionalLight = getDirectionalLightMap(state);
    const entityToAmbientLight = getAmbientLightMap(state);
    entityToPointLight.forEach((light) => {
      try {
        context.scene.remove(light);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose point light', e);
      }
    });
    entityToSpotLight.forEach((light) => {
      try {
        context.scene.remove(light);
        if (light.target) context.scene.remove(light.target);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose spot light', e);
      }
    });
    entityToDirectionalLight.forEach((light) => {
      try {
        context.scene.remove(light);
        if (light.target) context.scene.remove(light.target);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose directional light', e);
      }
    });
    entityToAmbientLight.forEach((light) => {
      try {
        context.scene.remove(light);
        light.dispose();
      } catch (e) {
        logger.warn('Failed to dispose ambient light', e);
      }
    });
    entityToPointLight.clear();
    entityToSpotLight.clear();
    entityToDirectionalLight.clear();
    entityToAmbientLight.clear();

    // Dispose bootstrap lights created in initializeContext.
    try {
      context.lights.ambient.dispose();
    } catch (e) {
      logger.warn('Failed to dispose ambient bootstrap light', e);
    }
    try {
      context.scene.remove(context.lights.directional.target);
      context.lights.directional.dispose();
    } catch (e) {
      logger.warn('Failed to dispose directional bootstrap light', e);
    }

    // Dispose InstancedMesh pools (each holds GPU instance buffers).
    try {
      context.meshPools.forEach((mesh) => mesh.dispose());
      context.meshPools.clear();
    } catch (e) {
      logger.warn('Failed to dispose mesh pools', e);
    }
    try {
      context.unlitMeshPools.forEach((mesh) => mesh.dispose());
      context.unlitMeshPools.clear();
    } catch (e) {
      logger.warn('Failed to dispose unlit mesh pools', e);
    }

    // Dispose shared bootstrap geometries + materials.
    try {
      context.geometries.forEach((g) => g.dispose());
      context.geometries.clear();
    } catch (e) {
      logger.warn('Failed to dispose geometries', e);
    }
    try {
      context.material.dispose();
    } catch (e) {
      logger.warn('Failed to dispose material', e);
    }
    try {
      context.unlitMaterial.dispose();
    } catch (e) {
      logger.warn('Failed to dispose unlit material', e);
    }

    // Dispose the PMREM environment texture applied by applyNeutralEnvironment.
    try {
      const env = context.scene.environment;
      if (env && (env as THREE.Texture).isTexture) {
        (env as THREE.Texture).dispose();
      }
      context.scene.environment = null;
    } catch (e) {
      logger.warn('Failed to dispose scene environment', e);
    }

    // Dispose remaining geometry/material/texture reachable from the scene
    // (entity GLB meshes, etc.). Dedup guards against double-dispose of the
    // shared bootstrap resources disposed above.
    try {
      disposeSceneGraph(context.scene);
    } catch (e) {
      logger.warn('Failed to dispose scene graph', e);
    }

    // Drop the camera cache so a re-init does not reuse stale cameras.
    threeCameras.clear();

    const contextEntities = renderContextQuery(state.world);
    for (const entity of contextEntities) {
      deleteCanvasElement(entity);
    }
  },
};
