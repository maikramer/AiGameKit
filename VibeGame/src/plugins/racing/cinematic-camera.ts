/**
 * CinematicCameraSystem — adds NFS-style camera modes that can be toggled at runtime:
 *
 *   - **CHASE** (default): follow-behind camera with smooth lag
 *   - **ORBIT**: orbital camera rotating around the vehicle (for replays/showcase)
 *   - **BUMPER**: low bumper cam close to the ground (speed feel)
 *   - **COCKPIT**: first-person view from inside the car
 *   - **CINEMATIC**: dramatic angles with auto-transition
 *
 * Toggle: press C to cycle through modes.
 * The active mode is stored on state.__cinematicMode for HUD display.
 */

import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { WorldTransform } from '../transforms';
import { Vehicle } from './components';
import { PlayerVehicle } from './components';

const playerVehicleQuery = defineQuery([Vehicle, PlayerVehicle]);

export type CinematicMode = 'chase' | 'orbit' | 'bumper' | 'cockpit' | 'cinematic';

interface CinematicState {
  mode: CinematicMode;
  orbitAngle: number;
  orbitHeight: number;
  orbitDistance: number;
  cinematicTimer: number;
  cinematicAngle: number;
  transitionProgress: number; // 0→1 when switching modes
  fromPosition: THREE.Vector3;
  targetPosition: THREE.Vector3;
}

const MODES: CinematicMode[] = ['chase', 'orbit', 'bumper', 'cockpit', 'cinematic'];

function createCinematicState(): CinematicState {
  return {
    mode: 'chase',
    orbitAngle: 0,
    orbitHeight: 4,
    orbitDistance: 12,
    cinematicTimer: 0,
    cinematicAngle: 0,
    transitionProgress: 1,
    fromPosition: new THREE.Vector3(),
    targetPosition: new THREE.Vector3(),
  };
}

// Store per-state (singleton for now).
const stateMap = new WeakMap<State, CinematicState>();

export function getCinematicState(state: State): CinematicState {
  let cs = stateMap.get(state);
  if (!cs) {
    cs = createCinematicState();
    stateMap.set(state, cs);
  }
  return cs;
}

/** Euler yaw from quaternion (same helper as other racing systems). */
function eulerY(x: number, y: number, z: number, w: number): number {
  const siny_cosp = 2 * (w * y + z * x);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  return Math.atan2(siny_cosp, cosy_cosp);
}

/**
 * Main system — runs in `draw` group after chase camera so it can override position.
 */
export const CinematicCameraSystem: System = defineSystem({
  name: 'CinematicCameraSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const camera = (state as any).camera as THREE.Camera | undefined;
    if (!camera) return;

    const cs = getCinematicState(state);
    const players = playerVehicleQuery(state.world);
    const eid = players[0];
    if (!eid) return;

    // --- Mode toggle (C key) -----------------------------------------------
    if (isKeyDown('KeyC')) {
      // Use a simple debounce via frame counter stored on state.
      const key = '__cinematicKeyCooldown';
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const cooldown = (state as any)[key] || 0;
      if (cooldown <= 0) {
        const idx = MODES.indexOf(cs.mode);
        cs.mode = MODES[(idx + 1) % MODES.length];
        cs.transitionProgress = 0;
        cs.fromPosition.copy(camera.position);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (state as any)[key] = 40; // ~0.6s at 60fps
      }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (state as any)[key] = cooldown - 1;
    }

    // Read vehicle position/rotation.
    const px = WorldTransform.posX[eid];
    const py = WorldTransform.posY[eid];
    const pz = WorldTransform.posZ[eid];
    const yaw = eulerY(
      WorldTransform.rotX[eid],
      WorldTransform.rotY[eid],
      WorldTransform.rotZ[eid],
      WorldTransform.rotW[eid],
    );
    const speed = Vehicle.speed[eid] || 0;
    const maxSpeed = Vehicle.maxSpeed[eid] || 1;
    const speedFrac = Math.min(1, Math.abs(speed) / maxSpeed);

    // Compute target camera position based on mode.
    const target = cs.targetPosition;
    switch (cs.mode) {
      case 'chase': {
        // Default follow-behind (let ChaseCameraSystem handle this mostly).
        // Only apply slight speed-based FOV tweak.
        break;
      }
      case 'orbit': {
        // Slowly rotate around the vehicle.
        cs.orbitAngle += 0.008; // ~45s per full rotation
        const dist = cs.orbitDistance + speedFrac * 4;
        const h = cs.orbitHeight + speedFrac * 2;
        target.set(
          px + Math.cos(cs.orbitAngle) * dist,
          py + h,
          pz + Math.sin(cs.orbitAngle) * dist,
        );
        camera.position.lerp(target, 0.03);
        camera.lookAt(px, py + 1, pz);
        break;
      }
      case 'bumper': {
        // Low angle near the ground, slightly in front of the car.
        const fwdX = Math.sin(yaw);
        const fwdZ = Math.cos(yaw);
        const bumpDist = 2.5 + speedFrac * 2;
        target.set(
          px - fwdX * bumpDist,
          py + 0.4, // very low
          pz - fwdZ * bumpDist,
        );
        camera.position.lerp(target, 0.06);
        // Look ahead of the car.
        const lookAhead = 8 + speedFrac * 12;
        camera.lookAt(
          px + fwdX * lookAhead,
          py + 0.5,
          pz + fwdZ * lookAhead,
        );
        break;
      }
      case 'cockpit': {
        // First-person from driver seat.
        const cockpitOffset = 0.6; // forward from center
        const cockpitHeight = 0.9; // eye height
        target.set(
          px + Math.sin(yaw) * cockpitOffset,
          py + cockpitHeight,
          pz + Math.cos(yaw) * cockpitOffset,
        );
        camera.position.lerp(target, 0.1);
        // Look forward along car heading.
        const lookDist = 50;
        camera.lookAt(
          px + Math.sin(yaw) * lookDist,
          py + cockpitHeight - 0.2, // slight down angle
          pz + Math.cos(yaw) * lookDist,
        );
        break;
      }
      case 'cinematic': {
        // Dramatic auto-changing angles like NFS replay.
        cs.cinematicTimer += 0.016;
        cs.cinematicAngle += 0.005 + speedFrac * 0.01;

        // Oscillate between side-view, front-low, high-wide.
        const phase = (Math.sin(cs.cinematicTimer * 0.3) + 1) * 0.5; // 0→1
        let cinDist: number, cinH: number, cinSide: number;

        if (phase < 0.33) {
          // Side view (like passing shot).
          cinDist = 10 + speedFrac * 6;
          cinH = py + 2 + speedFrac * 2;
          cinSide = 1; // right side
        } else if (phase < 0.66) {
          // Front-low (hero approach).
          cinDist = 8 + speedFrac * 4;
          cinH = py + 0.8;
          cinSide = 0.3; // slightly right
        } else {
          // High-wide (establishing shot).
          cinDist = 16 + speedFrac * 8;
          cinH = py + 6 + speedFrac * 3;
          cinSide = 0.7;
        }

        const sideVec = new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw));
        target.set(
          px - Math.sin(yaw) * cinDist + sideVec.x * cinSide * cinDist,
          cinH,
          pz - Math.cos(yaw) * cinDist + sideVec.z * cinSide * cinDist,
        );

        // Smooth cinematic movement.
        const lerpSpeed = 0.02 + speedFrac * 0.02;
        camera.position.lerp(target, lerpSpeed);

        // Look at car with slight lead.
        const lead = 3 + speedFrac * 5;
        camera.lookAt(
          px + Math.sin(yaw) * lead,
          py + 1,
          pz + Math.cos(yaw) * lead,
        );
        break;
      }
    }

    // Store mode on state for HUD access.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (state as any).__cinematicMode = cs.mode;

    // Smooth FOV changes based on speed (all modes except cockpit).
    if (cs.mode !== 'cockpit') {
      const baseFOV = 74;
      const speedFOV = baseFOV + speedFrac * 14;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      if ((camera as any).isPerspectiveCamera) {
        const pc = camera as THREE.PerspectiveCamera;
        pc.fov += (speedFOV - pc.fov) * 0.05;
        pc.updateProjectionMatrix();
      }
    }
  },

  dispose() {
    // WeakMap cleans up automatically.
  },
});
