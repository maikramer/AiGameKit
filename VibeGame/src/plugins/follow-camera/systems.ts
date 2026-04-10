import { defineQuery, type System } from '../../core';
import { InputState, isKeyDown } from '../input';
import {
  normalizeAngle,
  shortestAngleDiff,
  smoothLerp,
} from '../orbit-camera/math';
import { Body } from '../physics';
import {
  Transform,
  WorldTransform,
  syncEulerFromQuaternion,
} from '../transforms';
import { FollowCamera } from './components';
import { ZOOM_PRESETS } from './constants';
import * as THREE from 'three';

const followCameraQuery = defineQuery([FollowCamera, Transform]);
const followCameraAllQuery = defineQuery([FollowCamera]);
const inputStateQuery = defineQuery([InputState]);

const _tempQuat = new THREE.Quaternion();
const _tempEuler = new THREE.Euler();
const _tempSpherical = new THREE.Spherical();
const _tempVecCam = new THREE.Vector3();
const _tempVecTarget = new THREE.Vector3();
const _tempMat = new THREE.Matrix4();
const _upVec = new THREE.Vector3(0, 1, 0);

function extractBodyYaw(entity: number): number {
  _tempQuat.set(
    Body.rotX[entity],
    Body.rotY[entity],
    Body.rotZ[entity],
    Body.rotW[entity]
  );
  _tempEuler.setFromQuaternion(_tempQuat, 'YXZ');
  return _tempEuler.y;
}

export const FollowCameraSetupSystem: System = {
  group: 'setup',
  update: (state) => {
    const cameraEntities = followCameraQuery(state.world);

    for (const entity of cameraEntities) {
      if (FollowCamera.target[entity] === 0) {
        const target = state.createEntity();
        state.addComponent(target, Transform, {
          scaleX: 1,
          scaleY: 1,
          scaleZ: 1,
        });
        FollowCamera.target[entity] = target;
      }

      if (FollowCamera.inputSource[entity] === 0) {
        const inputSources = inputStateQuery(state.world);
        if (inputSources.length > 0) {
          FollowCamera.inputSource[entity] = inputSources[0];
        } else {
          const source = state.createEntity();
          state.addComponent(source, InputState);
          FollowCamera.inputSource[entity] = source;
        }
      }
    }
  },
};

/**
 * Handles scroll-wheel zoom and optional right-mouse manual orbit.
 * When the user manually orbits, auto-rotate is suppressed for `autoRotateDelay` seconds.
 */
export const FollowCameraInputSystem: System = {
  group: 'simulation',
  update: (state) => {
    const cameraEntities = followCameraAllQuery(state.world);

    for (const entity of cameraEntities) {
      let inputSource = FollowCamera.inputSource[entity];
      if (!inputSource && state.hasComponent(entity, InputState)) {
        inputSource = entity;
        FollowCamera.inputSource[entity] = entity;
      }
      if (!inputSource || !state.hasComponent(inputSource, InputState)) {
        continue;
      }

      const scrollDelta = InputState.scrollDelta[inputSource];
      if (scrollDelta !== 0) {
        const cur = FollowCamera.targetDistance[entity];
        const minD = FollowCamera.minDistance[entity];
        const maxD = FollowCamera.maxDistance[entity];
        const zs = FollowCamera.zoomSensitivity[entity];
        const scale = Math.max(0.3, cur * 0.08);
        FollowCamera.targetDistance[entity] = Math.max(
          minD,
          Math.min(maxD, cur + scrollDelta * zs * scale)
        );
      }

      const zoomKeyNow =
        isKeyDown('KeyV') || InputState.middleMouse[inputSource] === 1;
      const wasHeld = FollowCamera.zoomKeyHeld[entity] === 1;
      FollowCamera.zoomKeyHeld[entity] = zoomKeyNow ? 1 : 0;

      if (zoomKeyNow && !wasHeld) {
        const next = ((FollowCamera.zoomLevel[entity] + 1) %
          ZOOM_PRESETS.length) as 0;
        FollowCamera.zoomLevel[entity] = next;
        FollowCamera.targetDistance[entity] = ZOOM_PRESETS[next];
      }

      if (FollowCamera.allowManualOrbit[entity] === 1) {
        const rightMouse = InputState.rightMouse[inputSource] === 1;
        if (rightMouse) {
          const sens = FollowCamera.sensitivity[entity];
          const lookX = InputState.lookX[inputSource];
          const lookY = InputState.lookY[inputSource];

          FollowCamera.targetYaw[entity] -= lookX * sens;
          const pitch = FollowCamera.targetPitch[entity] + lookY * sens;
          FollowCamera.targetPitch[entity] = Math.max(
            FollowCamera.minPitch[entity],
            Math.min(FollowCamera.maxPitch[entity], pitch)
          );

          FollowCamera.lastManualInputTime[entity] = state.time.elapsed * 1000;
        }
      }
    }
  },
};

/**
 * Core auto-rotate logic: smoothly rotates camera yaw to stay behind the
 * player's facing direction whenever the player is moving.
 */
export const FollowCameraAutoRotateSystem: System = {
  group: 'simulation',
  after: [FollowCameraInputSystem],
  update: (state) => {
    const cameraEntities = followCameraAllQuery(state.world);
    const now = state.time.elapsed * 1000;

    for (const entity of cameraEntities) {
      if (FollowCamera.autoRotate[entity] !== 1) continue;

      const targetEntity = FollowCamera.target[entity];
      if (!targetEntity || !state.hasComponent(targetEntity, Body)) continue;
      if (!state.hasComponent(targetEntity, InputState)) continue;

      const delay = FollowCamera.autoRotateDelay[entity];
      const timeSinceManual = now - FollowCamera.lastManualInputTime[entity];
      if (delay > 0 && timeSinceManual < delay * 1000) continue;

      const mx = InputState.moveX[targetEntity];
      const my = InputState.moveY[targetEntity];
      const isMoving = Math.abs(mx) > 0.01 || Math.abs(my) > 0.01;
      if (!isMoving) continue;

      const playerYaw = extractBodyYaw(targetEntity);
      const behindYaw = normalizeAngle(playerYaw + Math.PI);

      FollowCamera.targetYaw[entity] = behindYaw;
    }
  },
};

/**
 * Smoothly interpolates current values toward targets and positions the camera
 * in world space using spherical coordinates around a smoothed look-at point.
 * The look-at point itself has position lag behind the player for a spring feel.
 */
export const FollowCameraPositionSystem: System = {
  group: 'draw',
  update: (state) => {
    const cameraEntities = followCameraQuery(state.world);
    const dt = state.time.deltaTime;

    for (const cam of cameraEntities) {
      const targetEntity = FollowCamera.target[cam];
      if (!targetEntity || !state.hasComponent(targetEntity, WorldTransform)) {
        continue;
      }

      const orbitSmooth = smoothLerp(FollowCamera.smoothness[cam], dt);
      const yawSmooth = smoothLerp(FollowCamera.yawSmoothness[cam], dt);
      const lagSmooth = smoothLerp(FollowCamera.positionLag[cam], dt);

      const yawDiff = shortestAngleDiff(
        FollowCamera.currentYaw[cam],
        FollowCamera.targetYaw[cam]
      );
      FollowCamera.currentYaw[cam] = normalizeAngle(
        FollowCamera.currentYaw[cam] + yawDiff * yawSmooth
      );

      FollowCamera.currentPitch[cam] +=
        (FollowCamera.targetPitch[cam] - FollowCamera.currentPitch[cam]) *
        orbitSmooth;

      FollowCamera.currentDistance[cam] +=
        (FollowCamera.targetDistance[cam] - FollowCamera.currentDistance[cam]) *
        orbitSmooth;

      const actualX =
        WorldTransform.posX[targetEntity] + FollowCamera.offsetX[cam];
      const actualY =
        WorldTransform.posY[targetEntity] + FollowCamera.offsetY[cam];
      const actualZ =
        WorldTransform.posZ[targetEntity] + FollowCamera.offsetZ[cam];

      if (FollowCamera.smoothedTargetInit[cam] === 0) {
        FollowCamera.smoothedTargetX[cam] = actualX;
        FollowCamera.smoothedTargetY[cam] = actualY;
        FollowCamera.smoothedTargetZ[cam] = actualZ;
        FollowCamera.smoothedTargetInit[cam] = 1;
      } else {
        FollowCamera.smoothedTargetX[cam] +=
          (actualX - FollowCamera.smoothedTargetX[cam]) * lagSmooth;
        FollowCamera.smoothedTargetY[cam] +=
          (actualY - FollowCamera.smoothedTargetY[cam]) * lagSmooth;
        FollowCamera.smoothedTargetZ[cam] +=
          (actualZ - FollowCamera.smoothedTargetZ[cam]) * lagSmooth;
      }

      _tempVecTarget.set(
        FollowCamera.smoothedTargetX[cam],
        FollowCamera.smoothedTargetY[cam],
        FollowCamera.smoothedTargetZ[cam]
      );

      const dist = FollowCamera.currentDistance[cam];
      const polar = Math.PI / 2 - FollowCamera.currentPitch[cam];
      _tempSpherical.set(dist, polar, FollowCamera.currentYaw[cam]);

      _tempVecCam.setFromSpherical(_tempSpherical).add(_tempVecTarget);

      Transform.posX[cam] = _tempVecCam.x;
      Transform.posY[cam] = _tempVecCam.y;
      Transform.posZ[cam] = _tempVecCam.z;

      _tempMat.lookAt(_tempVecCam, _tempVecTarget, _upVec);
      _tempQuat.setFromRotationMatrix(_tempMat);

      Transform.rotX[cam] = _tempQuat.x;
      Transform.rotY[cam] = _tempQuat.y;
      Transform.rotZ[cam] = _tempQuat.z;
      Transform.rotW[cam] = _tempQuat.w;

      syncEulerFromQuaternion(Transform, cam);
    }
  },
};
