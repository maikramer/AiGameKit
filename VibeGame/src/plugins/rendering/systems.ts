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
  Postprocessing,
  PointLight,
  RenderContext,
  MeshRenderer,
  SpotLight,
} from './components';
import { getOrCreateMesh, hideInstance, updateInstance } from './operations';
import { buildPostProcessing } from './postprocessing';
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
const postprocessingQuery = defineQuery([Postprocessing]);
const _lightDir = new THREE.Vector3();
const _lightPos = new THREE.Vector3();
const _shadowCenter = new THREE.Vector3();
const _lightPosition = new THREE.Vector3();
const _lightQuaternion = new THREE.Quaternion();
const _lightForward = new THREE.Vector3(0, 0, -1);

const pointLightQuery = defineQuery([PointLight, WorldTransform]);
const spotLightQuery = defineQuery([SpotLight, WorldTransform]);
const entityToPointLight = new Map<number, THREE.PointLight>();
const entityToSpotLight = new Map<number, THREE.SpotLight>();

const MAX_POINT_LIGHTS = 4;
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
    const context = getRenderingContext(state);
    const scene = getScene(state);
    if (!scene) return;

    const ambients = ambientQuery(state.world);
    for (const entity of ambients) {
      let light = context.lights.ambient;
      if (!light) {
        light = new THREE.HemisphereLight();
        scene.add(light);
        context.lights.ambient = light;
      }

      light.color.setHex(AmbientLight.skyColor[entity]);
      light.groundColor.setHex(AmbientLight.groundColor[entity]);
      light.intensity = AmbientLight.intensity[entity];
    }

    const directionals = directionalQuery(state.world);

    for (const entity of directionals) {
      let light = context.lights.directional;
      if (!light) {
        light = new THREE.DirectionalLight();
        light.castShadow = true;
        scene.add(light);
        scene.add(light.target);
        context.lights.directional = light;
      }

      light.color.setHex(DirectionalLight.color[entity]);
      light.intensity = DirectionalLight.intensity[entity];

      _lightDir
        .set(
          DirectionalLight.directionX[entity],
          DirectionalLight.directionY[entity],
          DirectionalLight.directionZ[entity]
        )
        .normalize();

      if (DirectionalLight.castShadow[entity] === 1) {
        light.castShadow = true;
        light.shadow.mapSize.width = DirectionalLight.shadowMapSize[entity];
        light.shadow.mapSize.height = DirectionalLight.shadowMapSize[entity];
        light.shadow.bias = -0.0001;
        light.shadow.normalBias = 0.02;

        const shadowCenter = resolveShadowCenter(state);

        _lightPos
          .copy(shadowCenter)
          .add(
            _lightDir.clone().multiplyScalar(DirectionalLight.distance[entity])
          );

        light.position.copy(_lightPos);
        light.target.position.copy(shadowCenter);
        light.target.updateMatrixWorld();

        const shadowCamera = light.shadow.camera as THREE.OrthographicCamera;
        const radius = SHADOW_CONFIG.CAMERA_RADIUS;
        shadowCamera.left = -radius;
        shadowCamera.right = radius;
        shadowCamera.top = radius;
        shadowCamera.bottom = -radius;
        shadowCamera.near = SHADOW_CONFIG.NEAR_PLANE;
        shadowCamera.far = SHADOW_CONFIG.FAR_PLANE;
        shadowCamera.position.copy(_lightPos);
        shadowCamera.lookAt(shadowCenter);
        shadowCamera.updateProjectionMatrix();
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
          console.warn(
            `PointLight limit (${MAX_POINT_LIGHTS}) reached — skipping entity ${eid}`
          );
          continue;
        }
        light = new THREE.PointLight();
        scene.add(light);
        entityToPointLight.set(eid, light);
        context.lights.pointLights.push(light);
      }

      light.color.setHex(PointLight.color[eid]);
      light.intensity = PointLight.intensity[eid];
      light.distance = PointLight.distance[eid];
      light.decay = PointLight.decay[eid];

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
          console.warn(
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

      light.color.setHex(SpotLight.color[eid]);
      light.intensity = SpotLight.intensity[eid];
      light.distance = SpotLight.distance[eid];
      light.decay = SpotLight.decay[eid];
      light.angle = SpotLight.angle[eid];
      light.penumbra = SpotLight.penumbra[eid];

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

export const RendererSetupSystem: System = {
  group: 'setup',
  last: true,
  async setup(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.renderer) return;

    const contextEntities = renderContextQuery(state.world);
    if (contextEntities.length === 0) return;

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
    if (clearColor !== 0) context.scene.background = new THREE.Color(clearColor);

    window.addEventListener('resize', () =>
      handleWindowResize(state, renderer)
    );
  },
};

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

/**
 * Builds the post-processing pipeline once the renderer + main camera
 * exist, when a `Postprocessing` entity opts in (enabled). The runtime renders
 * through `context.postProcessing` when present (see runtime render loop).
 */
export const PostprocessingBuildSystem: System = {
  group: 'draw',
  after: [CameraSyncSystem],
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.postProcessing || !context.renderer) return;

    const entities = postprocessingQuery(state.world);
    if (entities.length === 0) return;
    const e = entities[0];
    if (Postprocessing.enabled[e] !== 1) return;

    const cameras = mainCameraQuery(state.world);
    if (cameras.length === 0) return;
    const camera = threeCameras.get(cameras[0]);
    if (!camera) return;

    context.postProcessing = buildPostProcessing(
      context.renderer,
      context.scene,
      camera,
      {
        enabled: 1,
        bloom: Postprocessing.bloom[e] === 1,
        bloomStrength: Postprocessing.bloomStrength[e],
        bloomRadius: Postprocessing.bloomRadius[e],
        bloomThreshold: Postprocessing.bloomThreshold[e],
        chromaticAberration: Postprocessing.chromaticAberration[e] === 1,
        chromaticAberrationStrength: Postprocessing.caStrength[e],
        vignette: Postprocessing.vignette[e] === 1,
        vignetteStrength: Postprocessing.vignetteStrength[e],
        vignetteSmoothness: 0.85,
        fxaa: false,
        smaa: false,
        smaaQuality: 0,
        tonemapping: 0,
        dither: 0,
        aa: Postprocessing.aa[e],
      }
    );
  },
  dispose(state: State) {
    const context = getRenderingContext(state);
    context.postProcessing?.dispose();
    context.postProcessing = undefined;
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
    if (clearColor !== 0) context.scene.background = new THREE.Color(clearColor);

    window.addEventListener('resize', () =>
      handleWindowResize(state, renderer)
    );
  },
  update(state: State) {
    if (state.headless) return;
  },
  dispose(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.renderer) {
      context.renderer.setAnimationLoop(null);
      context.renderer.dispose();
      context.renderer = undefined;
      context.canvas = undefined;
    }

    const contextEntities = renderContextQuery(state.world);
    for (const entity of contextEntities) {
      deleteCanvasElement(entity);
    }
  },
};
