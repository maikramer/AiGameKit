import { defineQuery, type State, type System } from '../../core';
import { InputState } from '../input';
import { CharacterMovement, Rigidbody } from '../physics';
import { MainCamera, threeCameras } from '../rendering';
import {
  Transform,
  WorldTransform,
  syncEulerFromQuaternion,
} from '../transforms';
import { ThirdPersonCamera } from './components';

const thirdPersonCameraQuery = defineQuery([
  ThirdPersonCamera,
  Transform,
  MainCamera,
]);
const thirdPersonCameraAllQuery = defineQuery([ThirdPersonCamera]);
const playerQuery = defineQuery([CharacterMovement, Rigidbody, InputState]);

/** Squared horizontal speed below which the camera holds its current yaw. */
const CAMERA_MOVE_EPS = 0.04;
/** Per-60fps fraction the camera yaw swings toward the heading each frame. */
const CAMERA_TURN_SMOOTH = 0.1;

/** Lerp `current` toward `target` (radians) along the shortest arc. */
function approachAngle(current: number, target: number, t: number): number {
  let delta = (target - current) % (Math.PI * 2);
  if (delta > Math.PI) delta -= Math.PI * 2;
  else if (delta < -Math.PI) delta += Math.PI * 2;
  return current + delta * t;
}

export const ThirdPersonCameraSystem: System = {
  group: 'draw',
  update(state: State) {
    if (state.headless) return;
    const dt = state.time.deltaTime;
    const cameraEntities = thirdPersonCameraQuery(state.world);

    for (const cam of cameraEntities) {
      const targetEid = ThirdPersonCamera.target[cam];
      if (!targetEid || !state.hasComponent(targetEid, WorldTransform)) {
        continue;
      }

      // Get target position
      const targetX = WorldTransform.posX[targetEid];
      const targetY = WorldTransform.posY[targetEid];
      const targetZ = WorldTransform.posZ[targetEid];

      // Auto-trailing camera: swing the yaw to sit behind the target's
      // movement direction. Keyboard-only — the mouse never drives the camera.
      // Pitch stays as configured.
      if (state.hasComponent(targetEid, CharacterMovement)) {
        const dvx = CharacterMovement.desiredVelX[targetEid];
        const dvz = CharacterMovement.desiredVelZ[targetEid];
        if (dvx * dvx + dvz * dvz > CAMERA_MOVE_EPS) {
          const desiredYaw = Math.atan2(dvx, dvz) + Math.PI;
          const turn = 1 - Math.pow(1 - CAMERA_TURN_SMOOTH, dt * 60);
          ThirdPersonCamera.yaw[cam] = approachAngle(
            ThirdPersonCamera.yaw[cam],
            desiredYaw,
            turn
          );
        }
      }

      // Calculate desired camera position
      const dist = ThirdPersonCamera.distance[cam];
      const pitch = ThirdPersonCamera.pitch[cam];
      const yaw = ThirdPersonCamera.yaw[cam];
      const heightOffset = ThirdPersonCamera.height[cam];

      const desiredX = targetX + Math.sin(yaw) * dist * Math.cos(pitch);
      const desiredY = targetY + heightOffset + Math.sin(pitch) * dist;
      const desiredZ = targetZ + Math.cos(yaw) * dist * Math.cos(pitch);

      // Smooth interpolation
      const smooth = ThirdPersonCamera.positionSmooth[cam];
      const smoothFactor = 1 - Math.pow(1 - smooth, dt * 60);

      if (ThirdPersonCamera.initialized[cam] === 0) {
        ThirdPersonCamera.currentX[cam] = desiredX;
        ThirdPersonCamera.currentY[cam] = desiredY;
        ThirdPersonCamera.currentZ[cam] = desiredZ;
        ThirdPersonCamera.initialized[cam] = 1;
      } else {
        ThirdPersonCamera.currentX[cam] +=
          (desiredX - ThirdPersonCamera.currentX[cam]) * smoothFactor;
        ThirdPersonCamera.currentY[cam] +=
          (desiredY - ThirdPersonCamera.currentY[cam]) * smoothFactor;
        ThirdPersonCamera.currentZ[cam] +=
          (desiredZ - ThirdPersonCamera.currentZ[cam]) * smoothFactor;
      }

      // Update ECS Transform
      Transform.posX[cam] = ThirdPersonCamera.currentX[cam];
      Transform.posY[cam] = ThirdPersonCamera.currentY[cam];
      Transform.posZ[cam] = ThirdPersonCamera.currentZ[cam];

      // Look at target (slightly above feet)
      const lookTargetY = targetY + 1.5;

      // Set rotation via lookAt matrix
      const camPos = ThirdPersonCamera.currentX[cam];
      const camPosY = ThirdPersonCamera.currentY[cam];
      const camPosZ = ThirdPersonCamera.currentZ[cam];

      // Calculate look-at quaternion
      const dx = targetX - camPos;
      const dy = lookTargetY - camPosY;
      const dz = targetZ - camPosZ;
      const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (len > 0.001) {
        const fx = dx / len;
        const fy = dy / len;
        const fz = dz / len;

        // Look-at rotation: forward = (fx, fy, fz), up = (0, 1, 0)
        const rx = -fz;
        const ry = 0;
        const rz = fx;
        const rw = 1 + fy;

        const mag = Math.sqrt(rx * rx + ry * ry + rz * rz + rw * rw);
        if (mag > 0.001) {
          Transform.rotX[cam] = rx / mag;
          Transform.rotY[cam] = ry / mag;
          Transform.rotZ[cam] = rz / mag;
          Transform.rotW[cam] = rw / mag;
        }
      }

      syncEulerFromQuaternion(Transform, cam);
      Transform.dirty[cam] = 1;

      // Also update the Three.js camera directly for immediate visual update
      const threeCamera = threeCameras.get(cam);
      if (threeCamera) {
        threeCamera.position.set(camPos, camPosY, camPosZ);
        threeCamera.lookAt(targetX, lookTargetY, targetZ);
      }
    }
  },
};

export const PlayerCameraLinkingSystem: System = {
  group: 'simulation',
  update(state: State) {
    const players = playerQuery(state.world);
    const cameras = thirdPersonCameraAllQuery(state.world);

    for (const player of players) {
      // Link first unlinked camera to first player
      for (const cam of cameras) {
        if (ThirdPersonCamera.target[cam] === 0) {
          ThirdPersonCamera.target[cam] = player;

          // Add InputState to camera if it doesn't have it
          if (!state.hasComponent(cam, InputState)) {
            state.addComponent(cam, InputState);
          }
          break;
        }
      }
    }
  },
};
