import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { Transform } from '../transforms';
import { AiDriver, PlayerVehicle, Track, Vehicle } from './components';
import {
  forEachNearbyObstacle,
  getTrackSpline,
  type TrackObstacle,
} from './data';
import { createFrame, type TrackSpline } from './spline';
import { getRaceState, isRacingActive } from './race-state';

const vehicleQuery = defineQuery([Vehicle, Transform]);
const playerQuery = defineQuery([Vehicle, PlayerVehicle]);
const trackQuery = defineQuery([Track]);

/** Arcade-heavy gravity: jumps land quickly instead of hanging like a balloon. */
const GRAVITY = 22;
/** Peak lateral acceleration the tyres can generate on dry asphalt (m/s²). */
const MAX_LATERAL_ACCEL = 26;
/** Aerodynamic drag (applied to v²). */
const DRAG = 0.0016;
/** Rolling resistance (m/s²). */
const ROLLING = 1.6;
/** Speed (m/s) at which steering authority has halved. */
const STEER_FALLOFF = 26;
/** Wheel radius used to convert speed into visual wheel spin (m). */
const WHEEL_RADIUS = 0.33;

// Separate scratch frames: the simulation needs the frame under the car *and*
// the frame it is moving to in the same step (that is how crests are detected).
const _frameA = createFrame();
const _frameB = createFrame();
const _frameC = createFrame();

/** Vehicles are resolved against each other in track space; rebuilt each step. */
interface CarSlot {
  eid: number;
  s: number;
  lateral: number;
}
const _cars: CarSlot[] = [];

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Write a "yaw around the surface normal" orientation into the entity Transform.
 *
 * The chassis is aligned to the road: forward is the heading projected onto the
 * surface plane, up is the (banked) surface normal. That single change is what
 * makes banked corners, crests and dips read correctly — the old build kept the
 * car world-up and chased the elevation with a separate ride-height hack.
 */
function writeOrientation(
  eid: number,
  heading: number,
  ux: number,
  uy: number,
  uz: number
): void {
  let fx = Math.sin(heading);
  let fy = 0;
  let fz = Math.cos(heading);
  const d = fx * ux + fy * uy + fz * uz;
  fx -= ux * d;
  fy -= uy * d;
  fz -= uz * d;
  const fl = Math.hypot(fx, fy, fz) || 1;
  fx /= fl;
  fy /= fl;
  fz /= fl;
  // right = up × forward
  const rx = uy * fz - uz * fy;
  const ry = uz * fx - ux * fz;
  const rz = ux * fy - uy * fx;

  // Quaternion from the basis matrix [right, up, forward].
  const m00 = rx;
  const m10 = ry;
  const m20 = rz;
  const m01 = ux;
  const m11 = uy;
  const m21 = uz;
  const m02 = fx;
  const m12 = fy;
  const m22 = fz;
  const trace = m00 + m11 + m22;
  let qx: number;
  let qy: number;
  let qz: number;
  let qw: number;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    qw = 0.25 / s;
    qx = (m21 - m12) * s;
    qy = (m02 - m20) * s;
    qz = (m10 - m01) * s;
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    qw = (m21 - m12) / s;
    qx = 0.25 * s;
    qy = (m01 + m10) / s;
    qz = (m02 + m20) / s;
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    qw = (m02 - m20) / s;
    qx = (m01 + m10) / s;
    qy = 0.25 * s;
    qz = (m12 + m21) / s;
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
    qw = (m10 - m01) / s;
    qx = (m02 + m20) / s;
    qy = (m12 + m21) / s;
    qz = 0.25 * s;
  }
  Transform.rotX[eid] = qx;
  Transform.rotY[eid] = qy;
  Transform.rotZ[eid] = qz;
  Transform.rotW[eid] = qw;
}

/** Reconstruct world position + orientation from a car's track-space state. */
export function applyTrackPose(eid: number, spline: TrackSpline): void {
  const f = spline.sampleAt(Vehicle.trackS[eid], _frameC);
  const lateral = Vehicle.trackLateral[eid];
  const height = Vehicle.airHeight[eid];
  Transform.posX[eid] = f.x + f.rx * lateral + f.ux * height;
  Transform.posY[eid] = f.y + f.ry * lateral + f.uy * height;
  Transform.posZ[eid] = f.z + f.rz * lateral + f.uz * height;
  writeOrientation(eid, Vehicle.heading[eid], f.ux, f.uy, f.uz);
  Transform.dirty[eid] = 1;
}

/** Read the player's keys into the vehicle's driver inputs. */
function readPlayerInput(eid: number): void {
  let throttle = 0;
  let brake = 0;
  let steer = 0;
  if (isKeyDown('KeyW') || isKeyDown('ArrowUp')) throttle = 1;
  if (isKeyDown('KeyS') || isKeyDown('ArrowDown')) brake = 1;
  // The camera chases from behind, so the screen projection flips: what the
  // physics calls a positive (right) yaw appears as the car moving left on
  // screen. We map A/Left to positive steer (screen-left) and D/Right to
  // negative steer (screen-right) so the controls match what the player sees.
  if (isKeyDown('KeyA') || isKeyDown('ArrowLeft')) steer += 1;
  if (isKeyDown('KeyD') || isKeyDown('ArrowRight')) steer -= 1;
  Vehicle.throttle[eid] = throttle;
  Vehicle.brakeInput[eid] = brake;
  Vehicle.steerInput[eid] = steer;
  Vehicle.handbrake[eid] = isKeyDown('Space') ? 1 : 0;
  Vehicle.boostInput[eid] =
    isKeyDown('ShiftLeft') || isKeyDown('ShiftRight') ? 1 : 0;
}

/** Zero every driver input (used while the lights are still red). */
function clearInput(eid: number): void {
  Vehicle.throttle[eid] = 0;
  Vehicle.brakeInput[eid] = 0;
  Vehicle.steerInput[eid] = 0;
  Vehicle.handbrake[eid] = 0;
  Vehicle.boostInput[eid] = 0;
}

/**
 * Arcade vehicle simulation, integrated in **track space**.
 *
 * A car's authoritative state is `(s, lateral, height, heading)` plus its
 * velocity split into forward and sideways components; the world pose is
 * reconstructed from the track frame every step. That buys three things a
 * world-space kinematic rigidbody never got right here:
 *
 * 1. **No vertical teleports.** Arc position advances continuously and is its
 *    own projection hint, so a circuit that crosses over itself keeps each car
 *    on its own branch instead of snapping to the nearest road in plan view.
 * 2. **Barriers that actually stop you.** The wall is `|lateral| ≤ width/2 +
 *    shoulder`; a clamp cannot be tunnelled through at any speed or timestep.
 * 3. **One model, not five effects.** Slip angle, the friction circle, drift on
 *    the handbrake, airtime over crests, banking and the off-line grip penalty
 *    all fall out of the same few lines.
 */
export const VehicleControlSystem: System = defineSystem({
  name: 'VehicleControlSystem',
  group: 'fixed',

  update(state: State) {
    const vehicles = vehicleQuery(state.world);
    if (vehicles.length === 0) return;

    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;

    const dt = state.time.fixedDeltaTime;
    if (dt <= 0) return;

    const racing = isRacingActive();
    const phase = getRaceState().phase;
    const player = playerQuery(state.world)[0] ?? -1;
    const shoulder = Track.shoulder[trackEid] || 0;
    const wallsOn = Track.walls[trackEid] !== 0;

    _cars.length = 0;

    for (const eid of vehicles) {
      if (!racing) {
        // Lights still red (or the flag is out): no drive, but gravity and the
        // suspension keep running so cars settle onto the grid.
        clearInput(eid);
        if (phase === 'finished') Vehicle.brakeInput[eid] = 0.6;
      } else if (eid === player && !state.hasComponent(eid, AiDriver)) {
        readPlayerInput(eid);
      }
      // A player vehicle that *also* carries `AiDriver` is left to the AI: that
      // is the attract/demo mode, and it is what makes a full race runnable in
      // a headless test.
      // AI inputs were written by AiDriverSystem earlier in the frame.

      simulateVehicle(eid, spline, dt, shoulder, wallsOn);

      _cars.push({
        eid,
        s: Vehicle.trackS[eid],
        lateral: Vehicle.trackLateral[eid],
      });
    }

    resolveCarContacts(spline);

    for (const car of _cars) applyTrackPose(car.eid, spline);
  },
});

function simulateVehicle(
  eid: number,
  spline: TrackSpline,
  dt: number,
  shoulder: number,
  wallsOn: boolean
): void {
  const f = spline.sampleAt(Vehicle.trackS[eid], _frameA);
  const halfRoad = f.width * 0.5;
  const edge = halfRoad + shoulder;

  // ---- Surface under the wheels ------------------------------------------
  const lateral = Vehicle.trackLateral[eid];
  const absLat = Math.abs(lateral);
  let surfaceGrip: number;
  if (absLat <= halfRoad - 0.4)
    surfaceGrip = f.grip; // asphalt
  else if (absLat <= halfRoad + 0.6)
    surfaceGrip = f.grip * 0.88; // kerb
  else if (absLat <= edge)
    surfaceGrip = f.grip * 0.6; // gravel shoulder
  else surfaceGrip = f.grip * 0.42; // grass
  Vehicle.surfaceGrip[eid] = surfaceGrip;
  const offTrack = absLat > halfRoad + 0.6;

  const airborne = Vehicle.airborne[eid] === 1;
  const maxSpeedBase = Vehicle.maxSpeed[eid] || 40;
  const throttle = Vehicle.throttle[eid];
  const brakeInput = Vehicle.brakeInput[eid];
  const handbrake = Vehicle.handbrake[eid] === 1;

  // ---- Boost --------------------------------------------------------------
  const capacity = Vehicle.boostCapacity[eid] || 0;
  let boosting = false;
  if (capacity > 0) {
    if (
      Vehicle.boostInput[eid] === 1 &&
      Vehicle.boost[eid] > 0.01 &&
      !airborne &&
      Vehicle.speed[eid] > 1
    ) {
      boosting = true;
      Vehicle.boost[eid] = Math.max(0, Vehicle.boost[eid] - dt);
    } else {
      Vehicle.boost[eid] = Math.min(
        capacity,
        Vehicle.boost[eid] + (Vehicle.boostRecharge[eid] || 0) * dt
      );
    }
  }
  Vehicle.boosting[eid] = boosting ? 1 : 0;
  const maxSpeed = boosting
    ? maxSpeedBase * (Vehicle.boostSpeed[eid] || 1.25)
    : maxSpeedBase;

  // ---- Longitudinal -------------------------------------------------------
  let speed = Vehicle.speed[eid];
  if (!airborne) {
    if (throttle > 0 && brakeInput <= 0) {
      const headroom = clamp(1 - Math.abs(speed) / maxSpeed, 0, 1);
      const power =
        (Vehicle.accel[eid] || 24) *
        headroom *
        throttle *
        clamp(surfaceGrip, 0.35, 1);
      // Cancel drag and rolling resistance while the car is still under its
      // rated top speed, so `max-speed` is the speed the car actually reaches.
      // Left uncompensated the resistances eat the last 15%, and a kart
      // advertised at 187 km/h tops out at 158.
      const resistance = ROLLING * (offTrack ? 3.2 : 1) + DRAG * speed * speed;
      speed += (power + (speed < maxSpeed ? resistance * throttle : 0)) * dt;
      if (boosting) speed += (Vehicle.boostAccel[eid] || 10) * dt;
    } else if (brakeInput > 0) {
      if (speed > 0.2) {
        speed -=
          (Vehicle.brake[eid] || 44) *
          brakeInput *
          clamp(surfaceGrip, 0.4, 1) *
          dt;
        if (speed < 0) speed = 0;
      } else {
        speed -= (Vehicle.accel[eid] || 24) * 0.45 * brakeInput * dt;
        const revMax = Vehicle.reverseSpeed[eid] || 12;
        if (speed < -revMax) speed = -revMax;
      }
    } else {
      const coast = (Vehicle.engineBrake[eid] || 6) * dt;
      if (speed > coast) speed -= coast;
      else if (speed < -coast) speed += coast;
      else speed = 0;
    }
    if (handbrake && speed > 0) {
      speed -= (Vehicle.brake[eid] || 44) * 0.28 * dt;
      if (speed < 0) speed = 0;
    }
  }

  // Drag everywhere; rolling resistance and slope only on the ground.
  speed -= Math.sign(speed) * DRAG * speed * speed * dt;
  if (!airborne) {
    const roll = ROLLING * (offTrack ? 3.2 : 1);
    if (Math.abs(speed) > roll * dt) speed -= Math.sign(speed) * roll * dt;
    else speed = 0;
    // Gravity along the slope: the climb bites, the descent pays it back.
    speed -= GRAVITY * 0.45 * f.ty * dt;
  }
  if (speed > maxSpeed) speed -= (speed - maxSpeed) * Math.min(1, 3 * dt);
  const revLimit = -(Vehicle.reverseSpeed[eid] || 12);
  if (speed < revLimit) speed = revLimit;

  // ---- Steering -----------------------------------------------------------
  const steerTarget = clamp(Vehicle.steerInput[eid], -1, 1);
  Vehicle.steer[eid] +=
    (steerTarget - Vehicle.steer[eid]) *
    Math.min(1, (Vehicle.steerSpeed[eid] || 9) * dt);
  const steer = Vehicle.steer[eid];

  const speedFactor = 1 / (1 + Math.abs(speed) / STEER_FALLOFF);
  const dirSign = speed < -0.05 ? -1 : 1;
  const yawRate =
    steer *
    (Vehicle.maxSteer[eid] || 2.4) *
    speedFactor *
    (handbrake ? 1.4 : 1) *
    (airborne ? 0.25 : 1) *
    dirSign *
    clamp(Math.abs(speed) / 3, 0, 1) * // no pirouettes while parked
    clamp(surfaceGrip + 0.25, 0.5, 1);
  Vehicle.yawRate[eid] = yawRate;
  Vehicle.heading[eid] += yawRate * dt;
  Vehicle.wheelSteer[eid] = steer * 0.5;

  // ---- Lateral slip (the drift model) -------------------------------------
  // Rotating the chassis leaves the velocity vector behind, which shows up as
  // sideways speed in the car frame; grip pulls it back, bounded by how much
  // lateral force the tyres can make (the friction circle).
  let lateralSpeed = Vehicle.lateralSpeed[eid];
  lateralSpeed -= yawRate * speed * dt;
  if (!airborne) {
    const gripRate =
      (Vehicle.grip[eid] || 6) *
      surfaceGrip *
      (handbrake ? Vehicle.driftGrip[eid] || 0.35 : 1);
    const desired = -lateralSpeed * gripRate;
    const maxLat =
      MAX_LATERAL_ACCEL * surfaceGrip * (handbrake ? 0.5 : 1) +
      Math.abs(Math.sin(f.bank)) * GRAVITY; // banking buys grip back
    lateralSpeed += clamp(desired, -maxLat, maxLat) * dt;
    // Sliding scrubs speed: a drift is fast, a spin is not.
    speed -= Math.abs(lateralSpeed) * 0.12 * dt * (handbrake ? 0.5 : 1);
  }

  // ---- Advance along the track -------------------------------------------
  let fwdX = Math.sin(Vehicle.heading[eid]);
  let fwdY = 0;
  let fwdZ = Math.cos(Vehicle.heading[eid]);
  const dUp = fwdX * f.ux + fwdY * f.uy + fwdZ * f.uz;
  fwdX -= f.ux * dUp;
  fwdY -= f.uy * dUp;
  fwdZ -= f.uz * dUp;
  const fl = Math.hypot(fwdX, fwdY, fwdZ) || 1;
  fwdX /= fl;
  fwdY /= fl;
  fwdZ /= fl;
  // right = up × forward
  const rgtX = f.uy * fwdZ - f.uz * fwdY;
  const rgtY = f.uz * fwdX - f.ux * fwdZ;
  const rgtZ = f.ux * fwdY - f.uy * fwdX;

  const velX = fwdX * speed + rgtX * lateralSpeed;
  const velY = fwdY * speed + rgtY * lateralSpeed;
  const velZ = fwdZ * speed + rgtZ * lateralSpeed;

  const ds = (velX * f.tx + velY * f.ty + velZ * f.tz) * dt;
  const dLat = (velX * f.rx + velY * f.ry + velZ * f.rz) * dt;

  const newS = spline.wrapS(Vehicle.trackS[eid] + ds);
  let newLateral = lateral + dLat;
  const nf = spline.sampleAt(newS, _frameB);

  // ---- Vertical: suspension, crests, landings -----------------------------
  const rideHeight = Vehicle.rideHeight[eid] || 0.35;
  let height = Vehicle.airHeight[eid];
  let vertical = Vehicle.verticalSpeed[eid];
  let landingImpact = 0;

  if (airborne) {
    vertical -= GRAVITY * dt;
    height += vertical * dt;
    if (height <= rideHeight) {
      landingImpact = clamp(-vertical / 18, 0, 1);
      height = rideHeight;
      vertical = 0;
      Vehicle.airborne[eid] = 0;
      speed *= 1 - 0.18 * landingImpact;
      lateralSpeed *= 1 - 0.4 * landingImpact;
      if (landingImpact > 0.25) Vehicle.impactTimer[eid] = 0;
    }
  } else {
    // A crest taken fast throws the car into the air instead of gluing it to
    // the geometry: how far the road fell this step tells us the launch rate.
    const surfaceDrop = f.y - nf.y;
    const launch = (surfaceDrop / dt) * 0.9;
    if (launch > 6 && Math.abs(speed) > 8) {
      Vehicle.airborne[eid] = 1;
      vertical = clamp(launch * 0.5, 0, 14);
      height = rideHeight + vertical * dt;
    } else {
      height += (rideHeight - height) * Math.min(1, 18 * dt);
      vertical = 0;
    }
  }

  // ---- Barriers -----------------------------------------------------------
  // The wall is a solid with thickness. The car stops at the *inner* face of
  // the barrier — the side that faces the track — with a small inset for the
  // car's own half width so the side of the chassis kisses the wall, not its
  // centre line.
  const carHalfWidth = Vehicle.halfWidth[eid] || 0.85;
  const limit = nf.width * 0.5 + shoulder - carHalfWidth;
  const outerLimit = wallsOn ? limit : limit + 30;
  if (Math.abs(newLateral) > outerLimit) {
    const side = Math.sign(newLateral);
    newLateral = side * outerLimit;
    // How fast the car was actually closing on the barrier — the *whole*
    // lateral velocity, not just the slip. A car pointed 20° into the wall is
    // closing at 15 m/s with zero side-slip; measuring only the slip let it
    // grind along the barrier at full speed with no penalty at all.
    const intoWall = (side * dLat) / dt;
    if (wallsOn && intoWall > 0.5) {
      // Glancing blows cost little; stuffing it head-on costs a lot.
      const severity = clamp(intoWall / 14, 0, 1);
      lateralSpeed = -side * intoWall * 0.15;
      speed *= 1 - 0.45 * severity;
      // Rub along the barrier: the chassis is dragged parallel to the wall so
      // the car scrapes along and drives out of it. Without this it sits nose
      // into the barrier at walking pace for the rest of the lap, because the
      // moment it accelerates it drives straight back into the same wall.
      const trackHeading = Math.atan2(nf.tx, nf.tz);
      let headingErr = Vehicle.heading[eid] - trackHeading;
      while (headingErr > Math.PI) headingErr -= Math.PI * 2;
      while (headingErr < -Math.PI) headingErr += Math.PI * 2;
      Vehicle.heading[eid] -= headingErr * Math.min(1, 7 * dt);
      Vehicle.impactTimer[eid] = 0;
    } else if (wallsOn) {
      lateralSpeed = 0;
    }
  }

  // ---- Commit -------------------------------------------------------------
  Vehicle.speed[eid] = speed;
  Vehicle.lateralSpeed[eid] = lateralSpeed;
  Vehicle.slip[eid] = clamp(Math.abs(lateralSpeed) / 9, 0, 1);
  Vehicle.airHeight[eid] = height;
  Vehicle.verticalSpeed[eid] = vertical;
  Vehicle.trackS[eid] = newS;
  Vehicle.trackLateral[eid] = newLateral;
  Vehicle.impactTimer[eid] += dt;

  resolveObstacles(
    eid,
    nf.x + nf.rx * newLateral,
    nf.z + nf.rz * newLateral,
    spline
  );

  // ---- Visual + audio state ----------------------------------------------
  Vehicle.wheelSpin[eid] += (speed / WHEEL_RADIUS) * dt;
  const targetRoll = clamp(-yawRate * Math.abs(speed) * 0.012, -0.22, 0.22);
  Vehicle.roll[eid] += (targetRoll - Vehicle.roll[eid]) * Math.min(1, 7 * dt);
  const targetPitch = clamp(
    (brakeInput > 0 ? 0.06 : 0) -
      (throttle > 0 ? 0.035 : 0) -
      landingImpact * 0.08,
    -0.1,
    0.1
  );
  Vehicle.pitch[eid] +=
    (targetPitch - Vehicle.pitch[eid]) * Math.min(1, 8 * dt);

  // Revs: a five-speed box mapped onto speed, so the audio has shift points.
  const speedFrac = clamp(Math.abs(speed) / maxSpeedBase, 0, 1.25);
  const gearCount = 5;
  const gear =
    speed < -0.2
      ? 0
      : Math.min(gearCount, Math.floor(speedFrac * gearCount) + 1);
  Vehicle.gear[eid] = gear;
  const gearSpan = 1 / gearCount;
  const withinGear =
    gear <= 0
      ? speedFrac
      : clamp((speedFrac - (gear - 1) * gearSpan) / gearSpan, 0, 1);
  Vehicle.rpm[eid] = clamp(
    0.18 +
      withinGear * 0.82 +
      (Vehicle.slip[eid] > 0.4 && throttle > 0 ? 0.15 : 0),
    0,
    1.2
  );
}

/** Push a car out of any solid track-side object it drove into. */
function resolveObstacles(
  eid: number,
  worldX: number,
  worldZ: number,
  spline: TrackSpline
): void {
  const carRadius = Math.max(Vehicle.halfWidth[eid] || 0.8, 0.6);
  let hit: TrackObstacle | null = null;
  let hitDist = 0;
  forEachNearbyObstacle(worldX, worldZ, (o) => {
    const dx = worldX - o.x;
    const dz = worldZ - o.z;
    const d = Math.hypot(dx, dz);
    if (d < o.radius + carRadius) {
      hit = o;
      hitDist = d;
      return true;
    }
    return false;
  });
  if (hit === null) return;
  const o = hit as TrackObstacle;

  // Push out along the world contact normal, expressed as a track-space nudge
  // so the car never leaves the track manifold.
  const f = spline.sampleAt(Vehicle.trackS[eid], _frameC);
  const d = hitDist || 1e-3;
  const nx = (worldX - o.x) / d;
  const nz = (worldZ - o.z) / d;
  const push = o.radius + carRadius - d;
  Vehicle.trackLateral[eid] += (nx * f.rx + nz * f.rz) * push;
  Vehicle.trackS[eid] = spline.wrapS(
    Vehicle.trackS[eid] + (nx * f.tx + nz * f.tz) * push * 0.5
  );
  Vehicle.speed[eid] *= o.bounce;
  Vehicle.lateralSpeed[eid] *= -0.3;
  Vehicle.impactTimer[eid] = 0;
}

/**
 * Separate cars that overlap. Done in track space: two cars touch when they are
 * close in arc length *and* in lateral offset, which is cheap and matches how
 * contact actually reads on a circuit (side by side into a corner, nose to tail
 * out of it).
 */
function resolveCarContacts(spline: TrackSpline): void {
  const n = _cars.length;
  if (n < 2) return;
  for (let i = 0; i < n; i++) {
    const a = _cars[i]!;
    for (let j = i + 1; j < n; j++) {
      const b = _cars[j]!;
      const ds = spline.deltaS(a.s, b.s);
      const minS =
        (Vehicle.halfLength[a.eid] || 1.2) + (Vehicle.halfLength[b.eid] || 1.2);
      if (Math.abs(ds) >= minS) continue;
      const dLat = a.lateral - b.lateral;
      const minLat =
        (Vehicle.halfWidth[a.eid] || 0.8) + (Vehicle.halfWidth[b.eid] || 0.8);
      if (Math.abs(dLat) >= minLat) continue;

      // Push apart on the less-overlapped axis: sideways for a wheel-to-wheel
      // scrap, lengthways for a rear-ender.
      const overlapLat = minLat - Math.abs(dLat);
      const overlapS = minS - Math.abs(ds);
      if (overlapLat <= overlapS) {
        const dir = dLat >= 0 ? 1 : -1;
        const push = overlapLat * 0.5;
        a.lateral += dir * push;
        b.lateral -= dir * push;
        Vehicle.trackLateral[a.eid] = a.lateral;
        Vehicle.trackLateral[b.eid] = b.lateral;
        Vehicle.lateralSpeed[a.eid] += dir * 2.5;
        Vehicle.lateralSpeed[b.eid] -= dir * 2.5;
      } else {
        const dir = ds >= 0 ? 1 : -1;
        const push = overlapS * 0.5;
        a.s = spline.wrapS(a.s + dir * push);
        b.s = spline.wrapS(b.s - dir * push);
        Vehicle.trackS[a.eid] = a.s;
        Vehicle.trackS[b.eid] = b.s;
        const behind = dir > 0 ? b.eid : a.eid;
        const ahead = dir > 0 ? a.eid : b.eid;
        const closing = Vehicle.speed[behind] - Vehicle.speed[ahead];
        if (closing > 0) {
          Vehicle.speed[behind] -= closing * 0.35;
          Vehicle.speed[ahead] += closing * 0.2;
        }
      }
      Vehicle.impactTimer[a.eid] = 0;
      Vehicle.impactTimer[b.eid] = 0;
    }
  }
}

/**
 * Place a car on the track at a known arc position, stopped and pointing the
 * right way — grid slots, respawns and restarts all go through here.
 */
export function placeVehicleOnTrack(
  eid: number,
  spline: TrackSpline,
  s: number,
  lateral: number
): void {
  Vehicle.trackS[eid] = spline.wrapS(s);
  Vehicle.trackLateral[eid] = lateral;
  Vehicle.airHeight[eid] = Vehicle.rideHeight[eid] || 0.35;
  Vehicle.verticalSpeed[eid] = 0;
  Vehicle.airborne[eid] = 0;
  Vehicle.speed[eid] = 0;
  Vehicle.lateralSpeed[eid] = 0;
  Vehicle.steer[eid] = 0;
  Vehicle.yawRate[eid] = 0;
  Vehicle.slip[eid] = 0;
  Vehicle.roll[eid] = 0;
  Vehicle.pitch[eid] = 0;
  Vehicle.rpm[eid] = 0.18;
  Vehicle.gear[eid] = 1;
  Vehicle.boost[eid] = Vehicle.boostCapacity[eid] || 0;
  Vehicle.impactTimer[eid] = 10;
  const f = spline.sampleAt(Vehicle.trackS[eid], _frameC);
  Vehicle.heading[eid] = Math.atan2(f.tx, f.tz);
  applyTrackPose(eid, spline);
}
