import * as THREE from 'three';
import CameraControls from 'camera-controls';
import { defineSystem, defineQuery, type System } from '../../core';
import { Transform, WorldTransform } from '../transforms';
import { InputState } from '../input';
import { threeCameras } from '../rendering/utils';
import { OrbitCamera } from './components';
import {
  getCameraControls,
  setCameraControls,
  removeCameraControls,
} from './registry';

const orbitCameraQuery = defineQuery([OrbitCamera, Transform]);
const orbitCameraInputQuery = defineQuery([OrbitCamera]);
const inputStateQuery = defineQuery([InputState]);

export const OrbitCameraSetupSystem: System = defineSystem({
  name: 'OrbitCameraSetupSystem',
  group: 'setup',
  update: (state) => {
    const cameraEntities = orbitCameraQuery(state.world);

    for (const entity of cameraEntities) {
      if (OrbitCamera.target[entity] === 0) {
        const target = state.createEntity();
        state.addComponent(target, Transform, {
          scaleX: 1,
          scaleY: 1,
          scaleZ: 1,
        });
        OrbitCamera.target[entity] = target;
      }

      if (OrbitCamera.inputSource[entity] === 0) {
        const inputSources = inputStateQuery(state.world);
        if (inputSources.length > 0) {
          OrbitCamera.inputSource[entity] = inputSources[0];
        } else {
          const source = state.createEntity();
          state.addComponent(source, InputState);
          OrbitCamera.inputSource[entity] = source;
        }
      }
    }
  },
});

/**
 * Reads pointer/scroll intent from the ECS InputState (populated by the input
 * plugin, which remains the single owner of DOM listeners) and translates it
 * into imperative camera-controls calls. camera-controls runs in programmatic
 * mode (no domElement → no native drag), so there is zero DOM-listener
 * conflict with the input plugin.
 */
export const OrbitCameraInputSystem: System = defineSystem({
  name: 'OrbitCameraInputSystem',
  group: 'simulation',
  update: (state) => {
    const cameraEntities = orbitCameraInputQuery(state.world);

    for (const entity of cameraEntities) {
      let inputSource = OrbitCamera.inputSource[entity];

      if (!inputSource && state.hasComponent(entity, InputState)) {
        inputSource = entity;
        OrbitCamera.inputSource[entity] = entity;
      }

      if (!inputSource || !state.hasComponent(inputSource, InputState)) {
        continue;
      }

      const controls = getCameraControls(state, entity);
      if (!controls) continue; // not built yet (no THREE.Camera)

      const sensitivity = OrbitCamera.sensitivity[entity];
      const zoomSensitivity = OrbitCamera.zoomSensitivity[entity];
      const lookX = InputState.lookX[inputSource];
      const lookY = InputState.lookY[inputSource];
      const scrollDelta = InputState.scrollDelta[inputSource];
      const rightMouseHeld = InputState.rightMouse[inputSource] === 1;

      if (rightMouseHeld && (lookX !== 0 || lookY !== 0)) {
        // camera-controls: rotate(azimuthDelta, polarDelta, enableTransition).
        // Azimuth is negated to match the prior orbit behaviour (drag right →
        // scene rotates left). Polar sign follows the existing feel.
        controls.rotate(-lookX * sensitivity, lookY * sensitivity, false);
      }

      if (scrollDelta !== 0) {
        // Distance-proportional zoom, same formula as the hand-rolled version.
        const currentDistance = controls.distance;
        const distanceScale = Math.max(0.3, currentDistance * 0.08);
        const zoomDelta = scrollDelta * zoomSensitivity * distanceScale;
        // dolly(): positive moves the camera away, negative brings it closer.
        // scrollDelta sign already encodes the desired direction.
        controls.dolly(-zoomDelta, false);
      }
    }
  },
});

// Module-scope scratch: OrbitCameraSystem runs once per frame and is not
// reentrant, so reusing these avoids per-camera allocations.
const _targetPos = new THREE.Vector3();
const _camPos = new THREE.Vector3();

/**
 * Syncs the camera-controls target to the orbit target entity, drives the
 * per-frame update, then writes the resulting camera pose back into the
 * component fields (read by e.g. player yaw resolution) and the Transform
 * (so the rendering plugin's CameraSyncSystem stays consistent).
 */
export const OrbitCameraSystem: System = defineSystem({
  name: 'OrbitCameraSystem',
  group: 'draw',
  update: (state) => {
    const cameraEntities = orbitCameraQuery(state.world);
    const delta = state.time.deltaTime;

    for (const cameraEntity of cameraEntities) {
      const targetEntity = OrbitCamera.target[cameraEntity];
      if (!targetEntity || !state.hasComponent(targetEntity, WorldTransform)) {
        continue;
      }

      const camera = threeCameras.get(cameraEntity);
      if (!camera) continue; // rendering plugin hasn't built it yet

      // camera-controls' constructor references DOMRect unconditionally, so in
      // non-browser environments (bun test, SSR) we skip construction — the
      // orbit camera simply doesn't drive there. No behaviour loss since those
      // environments have no real rendering surface anyway.
      if (typeof DOMRect === 'undefined') continue;

      // Lazily construct the CameraControls for this entity once the
      // THREE.Camera exists. Runs in programmatic mode (no domElement).
      let controls = getCameraControls(state, cameraEntity);
      if (!controls) {
        controls = createControls(cameraEntity, camera);
        setCameraControls(state, cameraEntity, controls);
        // Clean up the instance when the entity is destroyed.
        state.onDestroy(cameraEntity, () => {
          removeCameraControls(state, cameraEntity);
        });
      }

      // Push the orbit target (entity world pos + offset) into camera-controls.
      _targetPos.set(
        WorldTransform.posX[targetEntity] + OrbitCamera.offsetX[cameraEntity],
        WorldTransform.posY[targetEntity] + OrbitCamera.offsetY[cameraEntity],
        WorldTransform.posZ[targetEntity] + OrbitCamera.offsetZ[cameraEntity]
      );
      controls.moveTo(_targetPos.x, _targetPos.y, _targetPos.z, false);

      controls.update(delta);

      // Read-back: mirror camera-controls state into the component so external
      // consumers (e.g. player yaw resolution) keep working. The camera-controls
      // azimuth accumulates beyond [0, 2π) on full turns; wrap it for stability.
      OrbitCamera.currentYaw[cameraEntity] =
        ((controls.azimuthAngle % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
      OrbitCamera.currentPitch[cameraEntity] = controls.polarAngle;
      OrbitCamera.currentDistance[cameraEntity] = controls.distance;

      // Write the resulting camera pose into Transform so the rendering
      // plugin's CameraSyncSystem (WorldTransform → THREE.Camera) and any
      // transform-hierarchy consumer stays in sync. camera-controls already
      // moved the THREE.Camera; we mirror it here.
      controls.getPosition(_camPos);
      Transform.posX[cameraEntity] = _camPos.x;
      Transform.posY[cameraEntity] = _camPos.y;
      Transform.posZ[cameraEntity] = _camPos.z;
      const cam = camera as THREE.PerspectiveCamera | THREE.OrthographicCamera;
      Transform.rotX[cameraEntity] = cam.quaternion.x;
      Transform.rotY[cameraEntity] = cam.quaternion.y;
      Transform.rotZ[cameraEntity] = cam.quaternion.z;
      Transform.rotW[cameraEntity] = cam.quaternion.w;
      Transform.dirty[cameraEntity] = 1;
    }
  },
});

/**
 * Construct a CameraControls instance for an orbit-camera entity, configured
 * from the component's per-entity settings. No domElement → programmatic mode,
 * no DOM listeners, no conflict with the input plugin.
 */
function createControls(entity: number, camera: THREE.Camera): CameraControls {
  const controls = new CameraControls(
    camera as THREE.PerspectiveCamera | THREE.OrthographicCamera
  );
  // Defensive: even without a domElement, ensure native drag can never engage.
  controls.enabled = false;

  controls.minDistance = OrbitCamera.minDistance[entity];
  controls.maxDistance = OrbitCamera.maxDistance[entity];
  controls.minPolarAngle = OrbitCamera.minPitch[entity];
  controls.maxPolarAngle = OrbitCamera.maxPitch[entity];
  // Interpret the legacy 0..1 smoothness as a smooth-time in seconds. Lower
  // smoothness → snappier (the old smoothLerp near 0 reacted almost
  // instantly); higher → slower settle.
  const smoothness = OrbitCamera.smoothness[entity];
  controls.smoothTime = THREE.MathUtils.clamp(0.05 + smoothness * 0.4, 0.05, 1);

  // Seed the initial pose from the component's current* state so the first
  // frame doesn't snap to a default.
  const initYaw = OrbitCamera.currentYaw[entity];
  const initPitch = OrbitCamera.currentPitch[entity];
  const initDist = OrbitCamera.currentDistance[entity];
  controls.rotateTo(initYaw, initPitch, false);
  controls.dollyTo(initDist, false);

  return controls;
}
