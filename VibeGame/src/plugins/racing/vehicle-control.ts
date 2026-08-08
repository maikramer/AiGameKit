import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import {
  Rigidbody,
  SetLinearVelocity,
  SetAngularVelocity,
} from '../physics/components';
import { markRigidbodyPoseDirty } from '../physics/utils';
import { WorldTransform } from '../transforms';
import { isRacingActive } from './race-state';
import { Vehicle, PlayerVehicle, Track } from './components';
import { getTrackData, sampleElevation } from './data';

const vehicleQuery = defineQuery([Vehicle, Rigidbody]);
const playerVehicleQuery = defineQuery([Vehicle, PlayerVehicle, Rigidbody]);
const trackQuery = defineQuery([Track]);

// Reusable temp (avoid per-frame alloc inside the hot loop).
const _fwd = { x: 0, y: 0, z: 0 };

function eulerYFromQuaternion(x: number, y: number, z: number, w: number): number {
  // Yaw (rotation about Y) from a quaternion — the only axis a ground vehicle uses.
  const siny_cosp = 2 * (w * y + z * x);
  const cosy_cosp = 1 - 2 * (y * y + z * z);
  return Math.atan2(siny_cosp, cosy_cosp);
}

/**
 * Arcade vehicle control. Runs in the `fixed` group so the velocity it writes is
 * consumed by the physics step the same frame.
 *
 * Model: the chassis is a kinematic-velocity body. We own its forward speed and
 * yaw rate directly — Rapier integrates them and resolves collisions with walls.
 * This is the Mario Kart / OutRun lineage: tight, readable, no spin-outs from a
 * bumped inertia tensor. Steering authority scales down with speed (twitchy at
 * low speed, boaty at top speed) which is the single biggest "feel" lever.
 *
 * Input: keyboard WASD/arrows for everyone; the player entity also reads
 * handbrake (Space) which widens the steering curve for power-slides.
 */
export const VehicleControlSystem: System = defineSystem({
  name: 'VehicleControlSystem',
  group: 'fixed',

  update(state: State) {
    const racing = isRacingActive();
    const vehicles = vehicleQuery(state.world);
    const player = playerVehicleQuery(state.world)[0] ?? -1;

    for (const eid of vehicles) {
      const isPlayer = eid === player;

      // --- Input ---------------------------------------------------------
      let throttle = 0;
      let steer = 0;
      let handbrake = 0;
      if (racing) {
        if (isPlayer) {
          if (isKeyDown('KeyW') || isKeyDown('ArrowUp')) throttle += 1;
          if (isKeyDown('KeyS') || isKeyDown('ArrowDown')) throttle -= 1;
          if (isKeyDown('KeyA') || isKeyDown('ArrowLeft')) steer -= 1;
          if (isKeyDown('KeyD') || isKeyDown('ArrowRight')) steer += 1;
          if (isKeyDown('Space')) handbrake = 1;
        }
        // (AI vehicles would set throttle/steer here in a future loop.)
      }

      const maxSpeed = Vehicle.maxSpeed[eid] || 40;
      const accel = Vehicle.accel[eid] || 25;
      const brakeDecel = Vehicle.brake[eid] || 40;
      const coast = Vehicle.engineBrake[eid] || 6;
      const reverseSpeed = Vehicle.reverseSpeed[eid] || 12;
      const maxSteer = Vehicle.maxSteer[eid] || 2.2;
      const steerSpeed = Vehicle.steerSpeed[eid] || 8;
      const grip = Vehicle.grip[eid] || 0.9;
      const speed = Vehicle.speed[eid];

      // --- Longitudinal (forward speed) ---------------------------------
      let newSpeed = speed;
      if (throttle > 0) {
        // Accelerate toward maxSpeed.
        newSpeed += accel * state.time.fixedDeltaTime * throttle;
        if (newSpeed > maxSpeed) newSpeed = maxSpeed;
      } else if (throttle < 0) {
        if (newSpeed > 0) {
          // Braking.
          newSpeed -= brakeDecel * state.time.fixedDeltaTime;
          if (newSpeed < 0) newSpeed = 0;
        } else {
          // Reverse.
          newSpeed -= accel * 0.6 * state.time.fixedDeltaTime;
          if (newSpeed < -reverseSpeed) newSpeed = -reverseSpeed;
        }
      } else {
        // Engine braking / coast to zero.
        const dec = coast * state.time.fixedDeltaTime;
        if (newSpeed > dec) newSpeed -= dec;
        else if (newSpeed < -dec) newSpeed += dec;
        else newSpeed = 0;
      }
      Vehicle.speed[eid] = newSpeed;

      // --- Steering (yaw rate) ------------------------------------------
      // Ramp the steer input toward target with a time constant for weight.
      const steerTarget = steer;
      const steerErr = steerTarget - Vehicle.steer[eid];
      Vehicle.steer[eid] += steerErr * Math.min(1, steerSpeed * state.time.fixedDeltaTime);

      // Steering authority falls off with speed: full at 0, ~30% at top speed.
      // Handbrake widens it (drift). Reverse flips the steer sign (backing up).
      const speedFrac = Math.min(1, Math.abs(newSpeed) / maxSpeed);
      const steerAuthority = (handbrake ? 1.25 : 1) * (1 - 0.7 * speedFrac);
      const dirSign = newSpeed < -0.01 ? -1 : 1;
      // NOTE: three.js/Rapier are right-handed with y-up. Rotation Y positive
      // swings +Z forward toward +X — but the car's RIGHT (looking down +Z) is
      // -X. Negate so D/right = clockwise = -X.
      const yawRate = -Vehicle.steer[eid] * maxSteer * steerAuthority * dirSign;

      // --- Lateral slip angle (NFS-style drift feel) -------------------
      // When handbrake is active or steering hard at speed, allow the body
      // to slide sideways while maintaining forward velocity direction.
      let lateralSlip = 0;
      if (handbrake > 0 && speedFrac > 0.25) {
        // Full drift mode: reduce grip significantly so car slides outward in turns.
        lateralSlip = (1 - grip * 0.5) * steerAuthority * Math.sign(Vehicle.steer[eid]);
        // Extra roll during drift for visual feedback.
        Vehicle.roll[eid] += (-Vehicle.steer[eid] * steerAuthority * 0.22 * Math.sign(newSpeed || 1) - Vehicle.roll[eid]) * 6 * state.time.fixedDeltaTime;
      }

      // --- Apply to the body --------------------------------------------
      // Forward vector from the chassis yaw (Y-axis heading).
      const yaw = eulerYFromQuaternion(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );
      _fwd.x = Math.sin(yaw);
      _fwd.y = 0;
      _fwd.z = Math.cos(yaw);

      // Lateral (right) vector for slip.
      const rightX = Math.cos(yaw);
      const rightZ = -Math.sin(yaw);

      // Base forward velocity.
      let vx = _fwd.x * newSpeed;
      let vy = 0;
      let vz = _fwd.z * newSpeed;

      // Add lateral slip component during drift.
      if (Math.abs(lateralSlip) > 0.01) {
        const slipSpeed = lateralSlip * maxSpeed * 0.5; // sideways slide
        vx += rightX * slipSpeed;
        vz += rightZ * slipSpeed;
      }

      state.addComponent(eid, SetLinearVelocity, {
        x: vx,
        y: vy, // no vertical drift — wheels stay on the road
        z: vz,
      });
      state.addComponent(eid, SetAngularVelocity, { x: 0, y: yawRate, z: 0 });

      // --- Ride height (3D track elevation) -------------------------------
      // The chassis is clamped to a ride height above the *track surface*, not
      // a fixed world Y. We sample the track elevation at the car's XZ and
      // smooth-follow it so the car climbs hills, dives into the tunnel, and
      // crests the mountain instead of floating at spawn height.
      const baseRide = Vehicle.rideHeight[eid];
      let targetY = baseRide;
      const tracks = trackQuery(state.world);
      if (tracks.length > 0) {
        const trackData = getTrackData(state, tracks[0]!);
        if (trackData?.elevations && trackData.elevations.length > 0) {
          const elev = sampleElevation(
            trackData,
            WorldTransform.posX[eid],
            WorldTransform.posZ[eid]
          );
          targetY = elev + baseRide;
        }
      }
      // Smoothly approach the target height (fast enough to feel planted,
      // slow enough to absorb sampling jitter on crests).
      const curY = WorldTransform.posY[eid];
      const blend = Math.min(1, 14 * state.time.fixedDeltaTime);
      const newY = curY + (targetY - curY) * blend;
      if (Math.abs(newY - curY) > 0.001) {
        Rigidbody.posY[eid] = newY;
        Rigidbody.velY[eid] = 0;
        markRigidbodyPoseDirty(eid);
      }

      // --- Visual juice state (read by the visual sync) -----------------
      Vehicle.wheelSpin[eid] += newSpeed * state.time.fixedDeltaTime * 2.2;
      // Body roll: lean out of the turn.
      const targetRoll = -Vehicle.steer[eid] * steerAuthority * 0.12 * Math.sign(newSpeed || 1);
      Vehicle.roll[eid] += (targetRoll - Vehicle.roll[eid]) * Math.min(1, 6 * state.time.fixedDeltaTime);
      // Pitch: nose dives on brake, lifts on accel.
      const targetPitch = (newSpeed - speed) * 0.01;
      Vehicle.pitch[eid] += (targetPitch - Vehicle.pitch[eid]) * Math.min(1, 8 * state.time.fixedDeltaTime);

      void grip; // reserved for a future slip-aware model
    }
  },
});
