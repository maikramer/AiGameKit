import * as RAPIER from '@dimforge/rapier3d-simd-compat';
import { defineQuery, type State, type System } from '../../core';
import { InputState, isKeyDown } from '../input';
import {
  normalizeAngle,
  shortestAngleDiff,
  smoothLerp,
} from '../orbit-camera/math';
import { getOrCreateWorld } from '../physics/world';
import { Rigidbody } from '../physics';
import { getRenderingContext, threeCameras } from '../rendering';
import {
  Transform,
  WorldTransform,
  syncEulerFromQuaternion,
} from '../transforms';
import { springStep } from '../third-person-controller/utils/spring';
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
    Rigidbody.rotX[entity],
    Rigidbody.rotY[entity],
    Rigidbody.rotZ[entity],
    Rigidbody.rotW[entity]
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
      if (!targetEntity || !state.hasComponent(targetEntity, Rigidbody))
        continue;
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
      Transform.dirty[cam] = 1;

      // Over-shoulder view offset
      const threeCamera = threeCameras.get(cam);
      if (threeCamera) {
        if (FollowCamera.overShoulder[cam] === 1) {
          const offset = FollowCamera.overShoulderOffset[cam] || 0.2;
          const w = window.innerWidth;
          const h = window.innerHeight;
          threeCamera.setViewOffset(w, h, w * offset, 0, w, h);
        } else {
          threeCamera.clearViewOffset();
        }
      }
    }
  },
};

const _springTarget = new THREE.Vector3();
const _springCam = new THREE.Vector3();
const _springSpherical = new THREE.Spherical();

/**
 * When `useSpring[eid] === 1`, replaces the smoothLerp convergence on the
 * look-at target with a critically-damped spring. Yaw / pitch / distance
 * still use smoothLerp (handled by FollowCameraPositionSystem).
 *
 * Runs AFTER FollowCameraPositionSystem so it can override the smoothed
 * target values and reposition the camera accordingly.
 */
export const FollowCameraSpringSystem: System = {
  group: 'draw',
  after: [FollowCameraPositionSystem],
  update: (state) => {
    const cameraEntities = followCameraQuery(state.world);
    const dt = state.time.deltaTime;

    for (const cam of cameraEntities) {
      if (FollowCamera.useSpring[cam] !== 1) continue;

      const targetEntity = FollowCamera.target[cam];
      if (!targetEntity || !state.hasComponent(targetEntity, WorldTransform)) {
        continue;
      }

      // First-frame snap: ensure velocities are zeroed and target is set
      if (FollowCamera.smoothedTargetInit[cam] === 0) {
        FollowCamera.smoothedTargetX[cam] =
          WorldTransform.posX[targetEntity] + FollowCamera.offsetX[cam];
        FollowCamera.smoothedTargetY[cam] =
          WorldTransform.posY[targetEntity] + FollowCamera.offsetY[cam];
        FollowCamera.smoothedTargetZ[cam] =
          WorldTransform.posZ[targetEntity] + FollowCamera.offsetZ[cam];
        FollowCamera.springVelocityX[cam] = 0;
        FollowCamera.springVelocityY[cam] = 0;
        FollowCamera.springVelocityZ[cam] = 0;
        FollowCamera.smoothedTargetInit[cam] = 1;
        continue;
      }

      const springTime = FollowCamera.springTime[cam] || 0.15;

      const actualX =
        WorldTransform.posX[targetEntity] + FollowCamera.offsetX[cam];
      const actualY =
        WorldTransform.posY[targetEntity] + FollowCamera.offsetY[cam];
      const actualZ =
        WorldTransform.posZ[targetEntity] + FollowCamera.offsetZ[cam];

      const sx = springStep(
        FollowCamera.smoothedTargetX[cam],
        actualX,
        FollowCamera.springVelocityX[cam],
        dt,
        springTime
      );
      const sy = springStep(
        FollowCamera.smoothedTargetY[cam],
        actualY,
        FollowCamera.springVelocityY[cam],
        dt,
        springTime
      );
      const sz = springStep(
        FollowCamera.smoothedTargetZ[cam],
        actualZ,
        FollowCamera.springVelocityZ[cam],
        dt,
        springTime
      );

      FollowCamera.smoothedTargetX[cam] = sx.value;
      FollowCamera.springVelocityX[cam] = sx.velocity;
      FollowCamera.smoothedTargetY[cam] = sy.value;
      FollowCamera.springVelocityY[cam] = sy.velocity;
      FollowCamera.smoothedTargetZ[cam] = sz.value;
      FollowCamera.springVelocityZ[cam] = sz.velocity;

      _springTarget.set(
        FollowCamera.smoothedTargetX[cam],
        FollowCamera.smoothedTargetY[cam],
        FollowCamera.smoothedTargetZ[cam]
      );

      const dist = FollowCamera.currentDistance[cam];
      const polar = Math.PI / 2 - FollowCamera.currentPitch[cam];
      _springSpherical.set(dist, polar, FollowCamera.currentYaw[cam]);

      _springCam.setFromSpherical(_springSpherical).add(_springTarget);

      Transform.posX[cam] = _springCam.x;
      Transform.posY[cam] = _springCam.y;
      Transform.posZ[cam] = _springCam.z;

      _tempMat.lookAt(_springCam, _springTarget, _upVec);
      _tempQuat.setFromRotationMatrix(_tempMat);

      Transform.rotX[cam] = _tempQuat.x;
      Transform.rotY[cam] = _tempQuat.y;
      Transform.rotZ[cam] = _tempQuat.z;
      Transform.rotW[cam] = _tempQuat.w;

      syncEulerFromQuaternion(Transform, cam);
      Transform.dirty[cam] = 1;
    }
  },
};

/**
 * When `wallAvoidance[eid] === 1`, casts a Rapier ray from the look-at
 * target toward the camera. If geometry is hit, pulls the camera closer
 * to the hit point (plus `wallAvoidanceOffset`). When clear, smoothly
 * returns to normal distance.
 */
export const FollowCameraWallAvoidanceSystem: System = {
  group: 'draw',
  after: [FollowCameraPositionSystem],
  update: (state) => {
    const cameraEntities = followCameraQuery(state.world);
    let world: RAPIER.World | null = null;

    for (const cam of cameraEntities) {
      if (FollowCamera.wallAvoidance[cam] !== 1) continue;

      // Lazy-init Rapier world (may not exist if physics plugin absent)
      if (!world) {
        world = getOrCreateWorld();
      }

      const camX = Transform.posX[cam];
      const camY = Transform.posY[cam];
      const camZ = Transform.posZ[cam];

      const targetX = FollowCamera.smoothedTargetX[cam];
      const targetY = FollowCamera.smoothedTargetY[cam];
      const targetZ = FollowCamera.smoothedTargetZ[cam];

      const dx = camX - targetX;
      const dy = camY - targetY;
      const dz = camZ - targetZ;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

      if (dist < 1e-4) continue;

      const invDist = 1 / dist;
      const dirX = dx * invDist;
      const dirY = dy * invDist;
      const dirZ = dz * invDist;

      const offset = FollowCamera.wallAvoidanceOffset[cam] || 0.3;

      const ray = new RAPIER.Ray(
        { x: targetX, y: targetY, z: targetZ },
        { x: dirX, y: dirY, z: dirZ }
      );

      const hit = world.castRay(ray, dist, true);

      if (hit !== null && hit.timeOfImpact < dist) {
        const safeDist = Math.max(0, hit.timeOfImpact - offset);
        const newCamX = targetX + dirX * safeDist;
        const newCamY = targetY + dirY * safeDist;
        const newCamZ = targetZ + dirZ * safeDist;

        Transform.posX[cam] = newCamX;
        Transform.posY[cam] = newCamY;
        Transform.posZ[cam] = newCamZ;

        _tempVecCam.set(newCamX, newCamY, newCamZ);
        _tempVecTarget.set(targetX, targetY, targetZ);
        _tempMat.lookAt(_tempVecCam, _tempVecTarget, _upVec);
        _tempQuat.setFromRotationMatrix(_tempMat);

        Transform.rotX[cam] = _tempQuat.x;
        Transform.rotY[cam] = _tempQuat.y;
        Transform.rotZ[cam] = _tempQuat.z;
        Transform.rotW[cam] = _tempQuat.w;

        syncEulerFromQuaternion(Transform, cam);
        Transform.dirty[cam] = 1;
      }
    }
  },
};

let pointerLockInitialized = false;
let pointerLockCanvas: HTMLCanvasElement | null = null;

function ensurePointerLockListeners(canvas: HTMLCanvasElement): void {
  if (pointerLockInitialized && pointerLockCanvas === canvas) return;
  pointerLockInitialized = true;
  pointerLockCanvas = canvas;

  canvas.addEventListener('click', () => {
    if (document.pointerLockElement !== canvas) {
      canvas.requestPointerLock();
    }
  });
}

export const FollowCameraMouseModeSystem: System = {
  group: 'simulation',
  after: [FollowCameraInputSystem],
  update(state: State): void {
    const cameraEntities = followCameraAllQuery(state.world);

    for (const cam of cameraEntities) {
      const mode = FollowCamera.mouseMode[cam];
      if (mode === 1) continue;

      if (mode === 0) {
        const rendering = getRenderingContext(state);
        const canvas = rendering.canvas;
        if (!canvas) continue;

        ensurePointerLockListeners(canvas);

        if (document.pointerLockElement === canvas) {
          const inputSource = FollowCamera.inputSource[cam];
          if (!inputSource || !state.hasComponent(inputSource, InputState)) continue;

          const lookX = InputState.lookX[inputSource];
          const lookY = InputState.lookY[inputSource];
          const sens = FollowCamera.sensitivity[cam];

          FollowCamera.targetYaw[cam] -= lookX * sens;
          const pitch = FollowCamera.targetPitch[cam] + lookY * sens;
          FollowCamera.targetPitch[cam] = Math.max(
            FollowCamera.minPitch[cam],
            Math.min(FollowCamera.maxPitch[cam], pitch)
          );

          FollowCamera.lastManualInputTime[cam] = state.time.elapsed * 1000;
        }
      } else if (mode >= 2 && mode <= 5) {
        console.warn(`[FollowCamera] Mouse mode ${mode} not yet implemented`);
      }
    }
  },
};
