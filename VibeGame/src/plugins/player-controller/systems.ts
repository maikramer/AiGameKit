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
import { castBvhRay, getBvhSurfaceHeight } from '../bvh';

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

      // Raw character transform — used ONLY to drive the smoothed follow point
      // below, never directly. This is what decouples the camera from the
      // character: jitter in the character can't reach the view.
      const rawTargetX = WorldTransform.posX[targetEid];
      const rawTargetY = WorldTransform.posY[targetEid];
      const rawTargetZ = WorldTransform.posZ[targetEid];

      const dist = ThirdPersonCamera.distance[cam];
      const pitch = ThirdPersonCamera.pitch[cam];
      const rawYaw = ThirdPersonCamera.yaw[cam]; // steered by A/D in PlayerMovement
      const heightOffset = ThirdPersonCamera.height[cam];
      const followLag = Math.max(1e-4, ThirdPersonCamera.followLag[cam]);
      const turnLag = Math.max(1e-4, ThirdPersonCamera.turnLag[cam]);

      // First frame: snap the smoothed state onto the target (no startup swoop).
      if (ThirdPersonCamera.initialized[cam] === 0) {
        ThirdPersonCamera.followX[cam] = rawTargetX;
        ThirdPersonCamera.followY[cam] = rawTargetY;
        ThirdPersonCamera.followZ[cam] = rawTargetZ;
        ThirdPersonCamera.smoothYaw[cam] = rawYaw;
      }

      // --- Decoupled follow (frame-rate-independent low-pass) ---
      // The follow point chases the character with a time constant, so it lags
      // on a dash then catches up, and any per-frame character jitter is
      // filtered out. Vertical is damped harder to swallow the step/bob bounce.
      const aXZ = 1 - Math.exp(-dt / followLag);
      const aY = 1 - Math.exp(-dt / (followLag * 1.8));
      ThirdPersonCamera.followX[cam] +=
        (rawTargetX - ThirdPersonCamera.followX[cam]) * aXZ;
      ThirdPersonCamera.followY[cam] +=
        (rawTargetY - ThirdPersonCamera.followY[cam]) * aY;
      ThirdPersonCamera.followZ[cam] +=
        (rawTargetZ - ThirdPersonCamera.followZ[cam]) * aXZ;

      // Yaw trails the steered heading, slower than the player turns (shortest
      // angular path so it never spins the long way around).
      const aYaw = 1 - Math.exp(-dt / turnLag);
      const yawErr = Math.atan2(
        Math.sin(rawYaw - ThirdPersonCamera.smoothYaw[cam]),
        Math.cos(rawYaw - ThirdPersonCamera.smoothYaw[cam])
      );
      ThirdPersonCamera.smoothYaw[cam] += yawErr * aYaw;

      const followX = ThirdPersonCamera.followX[cam];
      const followY = ThirdPersonCamera.followY[cam];
      const followZ = ThirdPersonCamera.followZ[cam];
      const yaw = ThirdPersonCamera.smoothYaw[cam];

      // The camera orbits and looks at the SMOOTHED follow point.
      const targetX = followX;
      const targetY = followY;
      const targetZ = followZ;

      const desiredX = followX + Math.sin(yaw) * dist * Math.cos(pitch);
      const desiredY = followY + heightOffset + Math.sin(pitch) * dist;
      const desiredZ = followZ + Math.cos(yaw) * dist * Math.cos(pitch);

      // --- Terrain collision applied to desired position FIRST ---
      // This prevents the fight between smooth interpolation and clamp:
      // smooth always chases a safe target, never a blocked one.
      let safeX = desiredX;
      let safeY = desiredY;
      let safeZ = desiredZ;
      const minDist = ThirdPersonCamera.minTerrainDistance[cam];
      if (minDist > 0) {
        const eyeY = targetY + 2.0;
        const dx = desiredX - targetX;
        const dy = desiredY - eyeY;
        const dz = desiredZ - targetZ;
        const fullDist = Math.sqrt(dx * dx + dy * dy + dz * dz);
        if (fullDist > 0.01) {
          const dir = new THREE.Vector3(
            dx / fullDist,
            dy / fullDist,
            dz / fullDist
          );
          const radius = Math.max(minDist, 0.5);
          const origin = new THREE.Vector3(targetX, eyeY, targetZ);

          // Multi-ray "sphere-cast": centre + lateral offsets
          let minSafe = fullDist;
          let hasHit = false;

          const hit1 = castBvhRay(state, origin, dir, fullDist, 0x0001);
          if (hit1 && hit1.distance < minSafe) {
            minSafe = hit1.distance;
            hasHit = true;
          }

          const right = new THREE.Vector3(dir.z, 0, -dir.x).normalize();
          const leftDir = new THREE.Vector3(
            dir.x * fullDist + right.x * radius,
            dir.y * fullDist + right.y * radius,
            dir.z * fullDist + right.z * radius
          ).normalize();
          const hit2 = castBvhRay(state, origin, leftDir, fullDist + radius, 0x0001);
          if (hit2 && hit2.distance < minSafe) {
            minSafe = hit2.distance;
            hasHit = true;
          }

          const rightDir = new THREE.Vector3(
            dir.x * fullDist - right.x * radius,
            dir.y * fullDist - right.y * radius,
            dir.z * fullDist - right.z * radius
          ).normalize();
          const hit3 = castBvhRay(state, origin, rightDir, fullDist + radius, 0x0001);
          if (hit3 && hit3.distance < minSafe) {
            minSafe = hit3.distance;
            hasHit = true;
          }

          // Fallback when BVH has no entry yet (terrain still loading)
          if (!hasHit) {
            const terrainY = getBvhSurfaceHeight(
              state,
              desiredX,
              desiredY + 100,
              desiredZ,
              2000,
              0x0001
            );
            if (terrainY !== null) {
              const minY = terrainY + minDist;
              if (desiredY < minY) {
                safeY = minY;
                hasHit = true;
              }
            }
          }

          if (hasHit) {
            const safeDist = Math.max(minSafe - radius, 0.01);
            safeX = targetX + dir.x * safeDist;
            safeY = eyeY + dir.y * safeDist;
            safeZ = targetZ + dir.z * safeDist;
          }
        }
      }

      // Final micro-smoothing toward the SAFE desired position: a fast low-pass
      // (~50ms) whose only job is to absorb collision/BVH pops. The intentional
      // lag (dash / turn) already lives in the smoothed follow point above, so
      // this stays snappy to avoid a double-laggy, floaty feel.
      const smoothFactor = 1 - Math.exp(-dt / 0.05);

      if (ThirdPersonCamera.initialized[cam] === 0) {
        ThirdPersonCamera.currentX[cam] = safeX;
        ThirdPersonCamera.currentY[cam] = safeY;
        ThirdPersonCamera.currentZ[cam] = safeZ;
        ThirdPersonCamera.initialized[cam] = 1;
      } else {
        ThirdPersonCamera.currentX[cam] +=
          (safeX - ThirdPersonCamera.currentX[cam]) * smoothFactor;
        ThirdPersonCamera.currentY[cam] +=
          (safeY - ThirdPersonCamera.currentY[cam]) * smoothFactor;
        ThirdPersonCamera.currentZ[cam] +=
          (safeZ - ThirdPersonCamera.currentZ[cam]) * smoothFactor;
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
