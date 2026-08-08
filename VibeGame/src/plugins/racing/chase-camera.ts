import { defineSystem, defineQuery, type State, type System } from '../../core';
import * as THREE from 'three';
import { MainCamera, threeCameras } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { ChaseCamera, Vehicle } from './components';

const chaseCamQuery = defineQuery([ChaseCamera, MainCamera]);

// Module-temp THREE objects (no per-frame allocation).
const _forward = new THREE.Vector3();
const _desiredPos = new THREE.Vector3();
const _lookAt = new THREE.Vector3();
const _camQuat = new THREE.Quaternion();
const _up = new THREE.Vector3(0, 1, 0);
const _m = new THREE.Matrix4();

function eulerYFromQuaternion(x: number, y: number, z: number, w: number): number {
  const siny_cosp = 2 * (w * y + z * x);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  return Math.atan2(siny_cosp, cosy_cosp);
}

/**
 * Racing chase camera. Unlike the third-person camera (free yaw steered by A/D),
 * this one trails the vehicle's heading: it sits behind wherever the car points,
 * smoothing position and heading with independent time constants so the view
 * feels weighted but never lost. A speed-sensitive FOV kick widens at top speed
 * for sense-of-speed juice.
 *
 * Runs in `draw`, after the generic `CameraSyncSystem`, and — critically — writes
 * the resolved pose back into the camera entity's `Transform` (not just the
 * THREE camera). The generic `CameraSyncSystem` rebuilds the THREE camera from
 * `Transform` every frame, so writing only the THREE camera is wiped next frame;
 * owning the `Transform` is the same pattern `ThirdPersonCameraSystem` uses.
 */
export const ChaseCameraSystem: System = defineSystem({
  name: 'ChaseCameraSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const dt = state.time.deltaTime;
    const cams = chaseCamQuery(state.world);

    for (const cam of cams) {
      const target = ChaseCamera.target[cam];
      if (!target || !state.hasComponent(target, WorldTransform)) continue;

      const tx = WorldTransform.posX[target];
      const ty = WorldTransform.posY[target];
      const tz = WorldTransform.posZ[target];
      const tyaw = eulerYFromQuaternion(
        WorldTransform.rotX[target],
        WorldTransform.rotY[target],
        WorldTransform.rotZ[target],
        WorldTransform.rotW[target]
      );

      const dist = ChaseCamera.distance[cam];
      const height = ChaseCamera.height[cam];
      const followLag = Math.max(1e-4, ChaseCamera.followLag[cam]);
      const turnLag = Math.max(1e-4, ChaseCamera.turnLag[cam]);
      const lookAhead = ChaseCamera.lookAhead[cam];

      // First frame: snap onto the target — no startup swoop.
      if (ChaseCamera.initialized[cam] === 0) {
        ChaseCamera.followX[cam] = tx;
        ChaseCamera.followY[cam] = ty;
        ChaseCamera.followZ[cam] = tz;
        ChaseCamera.smoothYaw[cam] = tyaw;
        ChaseCamera.initialized[cam] = 1;
      }

      // Position follow: low-pass on the target. Horizontal and vertical damped
      // separately so the camera doesn't sink/rise with every chassis bounce.
      const aXZ = 1 - Math.exp(-dt / followLag);
      const aY = 1 - Math.exp(-dt / (followLag * 1.5));
      ChaseCamera.followX[cam] += (tx - ChaseCamera.followX[cam]) * aXZ;
      ChaseCamera.followY[cam] += (ty - ChaseCamera.followY[cam]) * aY;
      ChaseCamera.followZ[cam] += (tz - ChaseCamera.followZ[cam]) * aXZ;

      // Heading trail: camera yaw chases the car yaw on the shortest arc, slower
      // than the car turns. This is the Mario Kart "camera swings round the bend
      // slightly after the kart" feel.
      let yawErr = tyaw - ChaseCamera.smoothYaw[cam];
      while (yawErr > Math.PI) yawErr -= Math.PI * 2;
      while (yawErr < -Math.PI) yawErr += Math.PI * 2;
      ChaseCamera.smoothYaw[cam] += yawErr * (1 - Math.exp(-dt / turnLag));

      const fx = ChaseCamera.followX[cam];
      const fy = ChaseCamera.followY[cam];
      const fz = ChaseCamera.followZ[cam];
      const yaw = ChaseCamera.smoothYaw[cam];

      // Desired camera position: behind the follow point along the smoothed heading,
      // raised by `height`.
      _forward.set(Math.sin(yaw), 0, Math.cos(yaw));
      _desiredPos.set(fx - _forward.x * dist, fy + height, fz - _forward.z * dist);

      // Look-at: the follow point, pushed forward by lookAhead so the view leads
      // the car slightly into the turn instead of staring at its rear bumper.
      _lookAt.set(fx + _forward.x * lookAhead, fy + height * 0.25, fz + _forward.z * lookAhead);

      // Resolve the desired camera orientation from lookAt.
      _m.lookAt(_desiredPos, _lookAt, _up);
      _camQuat.setFromRotationMatrix(_m);

      // --- Write the pose to the camera's Transform (the authority). --------
      // The generic CameraSyncSystem copies Transform → THREE camera every frame;
      // if we only touched the THREE camera, CameraSync would wipe our changes
      // next frame (that's why the earlier version never moved off y≈0.3).
      Transform.posX[cam] = _desiredPos.x;
      Transform.posY[cam] = _desiredPos.y;
      Transform.posZ[cam] = _desiredPos.z;
      Transform.rotX[cam] = _camQuat.x;
      Transform.rotY[cam] = _camQuat.y;
      Transform.rotZ[cam] = _camQuat.z;
      Transform.rotW[cam] = _camQuat.w;
      Transform.dirty[cam] = 1;

      // Also drive the THREE camera directly so this frame is correct even though
      // CameraSync ran before us this frame.
      const threeCam = threeCameras.get(cam);
      const perspCam = threeCam as THREE.PerspectiveCamera | null;
      if (threeCam) {
        threeCam.position.copy(_desiredPos);
        threeCam.quaternion.copy(_camQuat);

        // Speed-sensitive FOV: widen at high speed for a sense-of-speed kick.
        if (perspCam && perspCam.isPerspectiveCamera) {
          const speed = Vehicle.speed[target] || 0;
          const maxSpeed = Vehicle.maxSpeed[target] || 1;
          const speedFrac = Math.max(0, Math.min(1, Math.abs(speed) / maxSpeed));
          const fovBase = ChaseCamera.fovBase[cam] || 70;
          const fovBoost = ChaseCamera.fovBoost[cam] || 10;
          const targetFov = fovBase + fovBoost * speedFrac;
          if (perspCam.fov !== targetFov) {
            perspCam.fov += (targetFov - perspCam.fov) * Math.min(1, 4 * dt);
            perspCam.updateProjectionMatrix();
          }

          // Camera shake at high speed (NFS-style vibration).
          if (speedFrac > 0.6) {
            const shakeIntensity = (speedFrac - 0.6) * 0.03; // max ~3cm at top speed
            const shakeX = (Math.random() - 0.5) * shakeIntensity;
            const shakeY = (Math.random() - 0.5) * shakeIntensity * 0.5; // less vertical
            threeCam.position.x += shakeX;
            threeCam.position.y += shakeY;
          }

          // Nitro boost: extra FOV spike + stronger shake.
          const nitroActive = (state as unknown as { __nitro?: { active: boolean } }).__nitro?.active;
          if (nitroActive) {
            perspCam.fov += 4 * dt * 60; // temporary wide FOV burst
            perspCam.updateProjectionMatrix();
            const nitroShake = 0.05;
            threeCam.position.x += (Math.random() - 0.5) * nitroShake;
            threeCam.position.y += (Math.random() - 0.5) * nitroShake * 0.3;
          }
        }
      }
    }
  },
});
