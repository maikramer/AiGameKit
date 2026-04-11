import * as THREE from 'three';
import { CSM } from 'three-stdlib';
import type { State } from '../../core';
import { defineQuery, type System } from '../../core';
import { hasActiveComposer } from '../postprocessing/utils';
import { WorldTransform } from '../transforms';
import {
  AmbientLight,
  CsmConfig,
  DirectionalLight,
  MainCamera,
  PointLight,
  RenderContext,
  MeshRenderer,
  SpotLight,
} from './components';
import { getOrCreateMesh, hideInstance, updateInstance } from './operations';
import {
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
const ambientQuery = defineQuery([AmbientLight]);
const directionalQuery = defineQuery([DirectionalLight]);
const csmQuery = defineQuery([CsmConfig]);
const mainCameraTransformQuery = defineQuery([MainCamera, WorldTransform]);
const mainCameraQuery = defineQuery([MainCamera]);
const renderContextQuery = defineQuery([RenderContext]);
const _lightDir = new THREE.Vector3();
const _lightPos = new THREE.Vector3();
const _lightPosition = new THREE.Vector3();
const _lightQuaternion = new THREE.Quaternion();
const _lightForward = new THREE.Vector3(0, 0, -1);

const pointLightQuery = defineQuery([PointLight, WorldTransform]);
const spotLightQuery = defineQuery([SpotLight, WorldTransform]);
const entityToPointLight = new Map<number, THREE.PointLight>();
const entityToSpotLight = new Map<number, THREE.SpotLight>();

const MAX_POINT_LIGHTS = 4;
const MAX_SPOT_LIGHTS = 2;
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
    const csmEntities = csmQuery(state.world);
    const csmEntity = csmEntities.length > 0 ? csmEntities[0] : -1;

    const cameraEntities = mainCameraQuery(state.world);
    const camera =
      cameraEntities.length > 0
        ? threeCameras.get(cameraEntities[0])
        : undefined;

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

      const useCsm =
        entity === csmEntity &&
        CsmConfig.enabled[entity] === 1 &&
        camera instanceof THREE.PerspectiveCamera;

      _lightDir
        .set(
          DirectionalLight.directionX[entity],
          DirectionalLight.directionY[entity],
          DirectionalLight.directionZ[entity]
        )
        .normalize();

      if (useCsm && camera) {
        if (!context.csm) {
          context.csm = new CSM({
            cascades: CsmConfig.cascades[entity] || 4,
            maxFar: CsmConfig.maxFar[entity] || 200,
            shadowMapSize: CsmConfig.shadowMapSize[entity] || 2048,
            shadowBias: -0.0001,
            lightDirection: _lightDir.clone(),
            parent: scene,
            camera,
          });
          context.csmSetupPending = true;
        }

        context.csm.lightDirection.copy(_lightDir);
        context.csm.update();

        if (context.csmSetupPending) {
          context.csm.setupMaterial(context.material);
          context.csmSetupPending = false;
        }
      } else {
        if (context.csm) {
          context.csm.remove();
          context.csm.dispose();
          context.csm = undefined;
          context.csmSetupPending = false;
        }

        if (DirectionalLight.castShadow[entity] === 1) {
          light.castShadow = true;
          light.shadow.mapSize.width = DirectionalLight.shadowMapSize[entity];
          light.shadow.mapSize.height = DirectionalLight.shadowMapSize[entity];
          light.shadow.bias = -0.0001;
          light.shadow.normalBias = 0;
        } else {
          light.castShadow = false;
        }

        _lightPos
          .copy(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER)
          .add(
            _lightDir.clone().multiplyScalar(DirectionalLight.distance[entity])
          );

        light.position.copy(_lightPos);
        light.target.position.copy(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER);
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
        shadowCamera.lookAt(SHADOW_CONFIG.FIXED_FRUSTUM_CENTER);
        shadowCamera.updateProjectionMatrix();
        shadowCamera.updateMatrixWorld();
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

export const WebGLRenderSystem: System = {
  group: 'draw',
  last: true,
  setup(state: State) {
    if (state.headless) return;
    const contextEntities = renderContextQuery(state.world);
    if (contextEntities.length === 0) return;

    const entity = contextEntities[0];
    const canvas = getCanvasElement(entity);
    if (!canvas) return;

    const clearColor = RenderContext.clearColor[entity];
    const renderer = createRenderer(canvas, clearColor);

    const context = getRenderingContext(state);
    context.renderer = renderer;
    context.canvas = canvas;

    window.addEventListener('resize', () =>
      handleWindowResize(state, renderer)
    );
  },
  update(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (!context.renderer) return;

    const scene = getScene(state);
    if (!scene) return;

    const cameraEntities = mainCameraQuery(state.world);
    if (cameraEntities.length === 0) return;

    const cameraEntity = cameraEntities[0];
    const camera = threeCameras.get(cameraEntity);
    if (!camera) return;

    if (hasActiveComposer(state, cameraEntity)) return;

    context.renderer.render(scene, camera);
  },
  dispose(state: State) {
    if (state.headless) return;
    const context = getRenderingContext(state);
    if (context.renderer) {
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
