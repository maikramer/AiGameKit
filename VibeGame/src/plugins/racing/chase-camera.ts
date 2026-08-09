import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { MainCamera, threeCameras } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { ChaseCamera, Track, Vehicle } from './components';
import { getTrackSpline } from './data';
import { createFrame } from './spline';
import { getRaceState } from './race-state';

const chaseCamQuery = defineQuery([ChaseCamera, MainCamera]);
const trackQuery = defineQuery([Track]);

export type CameraModeName = 'chase' | 'close' | 'hood' | 'orbit';
export const CAMERA_MODES: CameraModeName[] = [
  'chase',
  'close',
  'hood',
  'orbit',
];

/** Per-mode rig offsets, multiplied onto the entity's authored distance/height. */
const MODE_RIG: Record<
  CameraModeName,
  { distance: number; height: number; lookAhead: number; fov: number }
> = {
  chase: { distance: 1, height: 1, lookAhead: 1, fov: 0 },
  close: { distance: 0.62, height: 0.75, lookAhead: 0.8, fov: 3 },
  hood: { distance: 0.05, height: 0.34, lookAhead: 1.6, fov: 6 },
  orbit: { distance: 1.5, height: 1.3, lookAhead: 0, fov: -4 },
};

const _forward = new THREE.Vector3();
const _up = new THREE.Vector3();
const _desired = new THREE.Vector3();
const _lookAt = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _matrix = new THREE.Matrix4();
const _frame = createFrame();

let modeKeyHeld = false;

function damp(
  current: number,
  target: number,
  lag: number,
  dt: number
): number {
  return (
    current + (target - current) * (1 - Math.exp(-dt / Math.max(1e-4, lag)))
  );
}

/**
 * Racing chase camera.
 *
 * Three things separate it from the generic third-person camera:
 *
 * - **It trails the car's heading**, not a free yaw, and it lags the heading on
 *   its own time constant, which is what makes a corner feel like a corner.
 * - **It rides the track's up vector**, so on a banked corner the horizon tilts
 *   with the road instead of the car appearing to fall over sideways.
 * - **Drift is readable**: the view yaw leans toward the car's actual direction
 *   of travel, so a slide shows the car sideways in frame rather than hiding it
 *   behind its own rear wing.
 *
 * Runs in `draw` after the generic camera sync, and owns the camera entity's
 * `Transform` (the sync system rebuilds the THREE camera from it every frame,
 * so writing only the THREE camera would be wiped next frame).
 */
export const ChaseCameraSystem: System = defineSystem({
  name: 'ChaseCameraSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const dt = Math.min(state.time.deltaTime, 0.1);
    const cams = chaseCamQuery(state.world);
    if (cams.length === 0) return;

    const trackEid = trackQuery(state.world)[0];
    const spline =
      trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
    const phase = getRaceState().phase;

    // `C` cycles the view (edge-triggered so a held key doesn't spin through).
    const cyclePressed = isKeyDown('KeyC');
    const cycle = cyclePressed && !modeKeyHeld;
    modeKeyHeld = cyclePressed;

    for (const cam of cams) {
      const target = ChaseCamera.target[cam];
      if (!target || !state.hasComponent(target, WorldTransform)) continue;

      if (cycle) {
        ChaseCamera.mode[cam] =
          (ChaseCamera.mode[cam] + 1) % CAMERA_MODES.length;
      }
      // The podium shot after the flag: orbit the winner.
      const modeIndex = phase === 'finished' ? 3 : ChaseCamera.mode[cam];
      const mode = CAMERA_MODES[modeIndex] ?? 'chase';
      const rig = MODE_RIG[mode];

      const tx = WorldTransform.posX[target];
      const ty = WorldTransform.posY[target];
      const tz = WorldTransform.posZ[target];
      const speed = Vehicle.speed[target] || 0;
      const maxSpeed = Vehicle.maxSpeed[target] || 1;
      const speedFrac = Math.min(1, Math.abs(speed) / maxSpeed);

      // Heading the camera wants to sit behind: the chassis heading, nudged
      // toward the travel direction so a drift stays legible.
      const slipAngle = Math.atan2(
        Vehicle.lateralSpeed[target] || 0,
        Math.max(4, Math.abs(speed))
      );
      const targetYaw = Vehicle.heading[target] + slipAngle * 0.55;

      // Track up vector (banking); falls back to world up off-circuit.
      let upX = 0;
      let upY = 1;
      let upZ = 0;
      if (spline) {
        const f = spline.sampleAt(Vehicle.trackS[target], _frame);
        upX = f.ux;
        upY = f.uy;
        upZ = f.uz;
      }

      if (ChaseCamera.initialized[cam] === 0) {
        ChaseCamera.followX[cam] = tx;
        ChaseCamera.followY[cam] = ty;
        ChaseCamera.followZ[cam] = tz;
        ChaseCamera.smoothYaw[cam] = targetYaw;
        ChaseCamera.upX[cam] = upX;
        ChaseCamera.upY[cam] = upY;
        ChaseCamera.upZ[cam] = upZ;
        ChaseCamera.fov[cam] = ChaseCamera.fovBase[cam] || 70;
        ChaseCamera.initialized[cam] = 1;
      }

      const followLag =
        Math.max(1e-3, ChaseCamera.followLag[cam]) *
        (mode === 'hood' ? 0.25 : 1);
      const turnLag =
        Math.max(1e-3, ChaseCamera.turnLag[cam]) * (mode === 'hood' ? 0.2 : 1);

      ChaseCamera.followX[cam] = damp(
        ChaseCamera.followX[cam],
        tx,
        followLag,
        dt
      );
      ChaseCamera.followY[cam] = damp(
        ChaseCamera.followY[cam],
        ty,
        followLag * 1.4,
        dt
      );
      ChaseCamera.followZ[cam] = damp(
        ChaseCamera.followZ[cam],
        tz,
        followLag,
        dt
      );

      let yawErr = targetYaw - ChaseCamera.smoothYaw[cam];
      while (yawErr > Math.PI) yawErr -= Math.PI * 2;
      while (yawErr < -Math.PI) yawErr += Math.PI * 2;
      ChaseCamera.smoothYaw[cam] += yawErr * (1 - Math.exp(-dt / turnLag));

      ChaseCamera.upX[cam] = damp(ChaseCamera.upX[cam], upX, 0.25, dt);
      ChaseCamera.upY[cam] = damp(ChaseCamera.upY[cam], upY, 0.25, dt);
      ChaseCamera.upZ[cam] = damp(ChaseCamera.upZ[cam], upZ, 0.25, dt);
      _up
        .set(ChaseCamera.upX[cam], ChaseCamera.upY[cam], ChaseCamera.upZ[cam])
        .normalize();

      const fx = ChaseCamera.followX[cam];
      const fy = ChaseCamera.followY[cam];
      const fz = ChaseCamera.followZ[cam];

      let yaw = ChaseCamera.smoothYaw[cam];
      if (mode === 'orbit') {
        ChaseCamera.orbitAngle[cam] += dt * 0.35;
        yaw = ChaseCamera.orbitAngle[cam];
      }

      // Forward along the camera yaw, flattened onto the track plane.
      _forward.set(Math.sin(yaw), 0, Math.cos(yaw));
      _forward.addScaledVector(_up, -_forward.dot(_up)).normalize();

      const distance = (ChaseCamera.distance[cam] || 7) * rig.distance;
      const height = (ChaseCamera.height[cam] || 3) * rig.height;
      // Pull back a little at speed — the extra framing reads as acceleration.
      const speedPull = 1 + speedFrac * 0.18;

      _desired
        .set(fx, fy, fz)
        .addScaledVector(_forward, -distance * speedPull)
        .addScaledVector(_up, height);

      const lookAhead = (ChaseCamera.lookAhead[cam] || 3) * rig.lookAhead;
      _lookAt
        .set(tx, ty, tz)
        .addScaledVector(_forward, lookAhead + speedFrac * 4)
        .addScaledVector(_up, height * 0.22);

      _matrix.lookAt(_desired, _lookAt, _up);
      _quat.setFromRotationMatrix(_matrix);

      // Impact and speed shake, applied to the final position only.
      const impact = Vehicle.impactTimer[target];
      let shake = speedFrac > 0.55 ? (speedFrac - 0.55) * 0.06 : 0;
      if (impact < 0.35) shake += (1 - impact / 0.35) * 0.32;
      if (Vehicle.boosting[target]) shake += 0.05;
      if (shake > 0) {
        _desired.x += (Math.random() - 0.5) * shake;
        _desired.y += (Math.random() - 0.5) * shake * 0.6;
        _desired.z += (Math.random() - 0.5) * shake;
      }

      Transform.posX[cam] = _desired.x;
      Transform.posY[cam] = _desired.y;
      Transform.posZ[cam] = _desired.z;
      Transform.rotX[cam] = _quat.x;
      Transform.rotY[cam] = _quat.y;
      Transform.rotZ[cam] = _quat.z;
      Transform.rotW[cam] = _quat.w;
      Transform.dirty[cam] = 1;

      const threeCam = threeCameras.get(cam) as
        THREE.PerspectiveCamera | undefined;
      if (threeCam) {
        threeCam.position.copy(_desired);
        threeCam.quaternion.copy(_quat);
        if (threeCam.isPerspectiveCamera) {
          const base = (ChaseCamera.fovBase[cam] || 70) + rig.fov;
          const targetFov =
            base +
            (ChaseCamera.fovBoost[cam] || 10) * speedFrac +
            (Vehicle.boosting[target] ? 12 : 0);
          ChaseCamera.fov[cam] = damp(
            ChaseCamera.fov[cam],
            targetFov,
            0.18,
            dt
          );
          if (Math.abs(threeCam.fov - ChaseCamera.fov[cam]) > 0.01) {
            threeCam.fov = ChaseCamera.fov[cam];
            threeCam.updateProjectionMatrix();
          }
        }
      }
    }
  },
});

/** Current view name for the HUD ("CHASE", "HOOD", …). */
export function getCameraModeName(cam: number): CameraModeName {
  return CAMERA_MODES[ChaseCamera.mode[cam]] ?? 'chase';
}
