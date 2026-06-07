import { defineQuery, type State, type System } from '../../core';
import * as THREE from 'three';
import { InputState } from '../input';
import { CharacterMovement, Rigidbody } from '../physics';
import { MainCamera, threeCameras } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import {
  Transform,
  WorldTransform,
  syncEulerFromQuaternion,
} from '../transforms';
import { ThirdPersonCamera } from './components';
import { castBvhRay } from '../bvh';

const thirdPersonCameraQuery = defineQuery([
  ThirdPersonCamera,
  Transform,
  MainCamera,
]);
const thirdPersonCameraAllQuery = defineQuery([ThirdPersonCamera]);
const playerQuery = defineQuery([CharacterMovement, Rigidbody, InputState]);

export const ThirdPersonCameraSystem: System = {
  group: 'draw',
  // Run after the generic camera sync so this system is the sole authority over
  // the third-person camera's Three.js transform (otherwise the two fight and
  // the view jitters every frame).
  after: [CameraSyncSystem],
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

      // Yaw is steered by the player (A/D) in PlayerMovementSystem; the camera
      // just orbits to the configured distance/pitch around that yaw.

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

      // Terrain/obstacle collision: raycast from target toward camera.
      // If terrain blocks the view, pull camera closer to hit point (like
      // GTA / Mario Galaxy). This avoids Y-clamp fighting smooth interp.
      const minDist = ThirdPersonCamera.minTerrainDistance[cam];
      if (minDist > 0) {
        const camX = ThirdPersonCamera.currentX[cam];
        const camY = ThirdPersonCamera.currentY[cam];
        const camZ = ThirdPersonCamera.currentZ[cam];
        const dx = camX - targetX;
        const dy = camY - (targetY + 1.8);
        const dz = camZ - targetZ;
        const distToCam = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (distToCam > 0.01) {
          const origin = new THREE.Vector3(targetX, targetY + 1.8, targetZ);
          const dir = new THREE.Vector3(dx / distToCam, dy / distToCam, dz / distToCam);
          const hit = castBvhRay(state, origin, dir, distToCam, 0x0001);
          if (hit && hit.distance < distToCam) {
            const pushBack = Math.max(minDist, 0.3);
            const safeDist = hit.distance - pushBack;
            if (safeDist > 0.01) {
              ThirdPersonCamera.currentX[cam] = targetX + dir.x * safeDist;
              ThirdPersonCamera.currentY[cam] = targetY + 1.8 + dir.y * safeDist;
              ThirdPersonCamera.currentZ[cam] = targetZ + dir.z * safeDist;
            }
          }
        }
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
