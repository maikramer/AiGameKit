import { defineSystem, defineQuery, type State, type System } from '../../core';
import { Transform } from '../transforms';
import {
  AiDriver,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from './components';
import { getTrackSpline } from './data';
import { createFrame, type TrackSpline } from './spline';
import { isRacingActive } from './race-state';

const aiQuery = defineQuery([AiDriver, Vehicle, Transform]);
const allCarsQuery = defineQuery([Vehicle, Transform]);
const playerQuery = defineQuery([PlayerVehicle, Vehicle]);
const trackQuery = defineQuery([Track]);

/** Lateral acceleration a confident AI is willing to ask of its tyres (m/s²). */
const AI_LATERAL_BUDGET = 20;
/** Deceleration the AI plans its braking points with (m/s²). */
const AI_BRAKE_PLANNING = 26;
/** How hard the AI leans on the steering error (1/rad). */
const STEER_GAIN = 1.6;
/** Slowest the AI will ever plan to take a corner (m/s). */
const MIN_CORNER_SPEED = 11;
/** A rival this far ahead (m) is close enough to have to avoid. */
const AVOID_RANGE = 26;

const _target = { x: 0, y: 0, z: 0 };
const _frame = createFrame();

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function wrapAngle(a: number): number {
  let x = a;
  while (x > Math.PI) x -= Math.PI * 2;
  while (x < -Math.PI) x += Math.PI * 2;
  return x;
}

/**
 * Rival AI: drives a racing line rather than chasing the centerline.
 *
 * Per step it picks a target point ahead on the line, steers at it (pure
 * pursuit), and sets a target speed from the tightest curvature inside its
 * braking distance — so it lifts *before* the corner instead of understeering
 * into the wall. Rubber-banding nudges its speed cap toward the player so the
 * race stays close without the rivals becoming either scenery or unbeatable.
 *
 * Runs in `fixed`, ordered before {@link VehicleControlSystem}, and only writes
 * driver inputs — the AI drives exactly the same vehicle model as the player.
 */
export const AiDriverSystem: System = defineSystem({
  name: 'AiDriverSystem',
  group: 'fixed',
  before: ['VehicleControlSystem'],

  update(state: State) {
    const drivers = aiQuery(state.world);
    if (drivers.length === 0) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;

    const racing = isRacingActive();
    const dt = state.time.fixedDeltaTime;
    const shoulder = Track.shoulder[trackEid] || 0;
    const player = playerQuery(state.world)[0];
    const playerDistance =
      player !== undefined ? RaceTracker.distance[player] : 0;
    const cars = allCarsQuery(state.world);

    for (const eid of drivers) {
      if (!racing || RaceTracker.finished[eid]) {
        Vehicle.throttle[eid] = 0;
        Vehicle.brakeInput[eid] = RaceTracker.finished[eid] ? 0.5 : 0;
        Vehicle.steerInput[eid] = 0;
        Vehicle.handbrake[eid] = 0;
        Vehicle.boostInput[eid] = 0;
        continue;
      }
      driveOne(eid, spline, dt, shoulder, cars, player, playerDistance);
    }
  },
});

function driveOne(
  eid: number,
  spline: TrackSpline,
  dt: number,
  shoulder: number,
  cars: readonly number[],
  player: number | undefined,
  playerDistance: number
): void {
  const skill = clamp(AiDriver.skill[eid] || 0.8, 0.35, 1);
  const speed = Vehicle.speed[eid];
  const s = Vehicle.trackS[eid];

  // ---- Where to aim -------------------------------------------------------
  const lookahead = clamp(7 + Math.abs(speed) * 0.55, 8, 45);
  const targetS = s + lookahead;
  const curveAtTarget = spline.curvatureAt(targetS);
  const frameWidth = spline.sampleAt(targetS, _frame).width;
  const halfUsable = Math.max(1.5, frameWidth * 0.5 - 1.2);

  // Apex-cutting line: hug the inside of the corner, drift out on the straights.
  const apexPull = clamp(Math.abs(curveAtTarget) / 0.02, 0, 1);
  let targetLateral = -Math.sign(curveAtTarget) * apexPull * halfUsable * 0.75;
  targetLateral += AiDriver.lineOffset[eid];

  // A little wander so a pack of rivals doesn't drive as one rigid train.
  AiDriver.noisePhase[eid] += dt * 0.6;
  targetLateral += Math.sin(AiDriver.noisePhase[eid]) * 0.5 * (1 - skill);

  // ---- Avoid whoever is directly ahead ------------------------------------
  let blocked = false;
  for (const other of cars) {
    if (other === eid) continue;
    const gap = spline.deltaS(Vehicle.trackS[other], s);
    if (gap <= 0 || gap > AVOID_RANGE) continue;
    const lateralGap = Vehicle.trackLateral[other] - Vehicle.trackLateral[eid];
    if (Math.abs(lateralGap) > 2.6) continue;
    blocked = true;
    // Pull toward whichever side has more room.
    const side = Vehicle.trackLateral[other] > 0 ? -1 : 1;
    targetLateral = clamp(
      Vehicle.trackLateral[other] + side * 3.0,
      -halfUsable,
      halfUsable
    );
    break;
  }

  targetLateral = clamp(
    targetLateral,
    -(halfUsable + shoulder * 0.3),
    halfUsable + shoulder * 0.3
  );

  // ---- Steering (pure pursuit) --------------------------------------------
  spline.positionAt(targetS, targetLateral, Vehicle.rideHeight[eid], _target);
  const dx = _target.x - Transform.posX[eid];
  const dz = _target.z - Transform.posZ[eid];
  const desiredHeading = Math.atan2(dx, dz);
  const headingErr = wrapAngle(desiredHeading - Vehicle.heading[eid]);
  const steer = clamp(headingErr * STEER_GAIN, -1, 1);
  AiDriver.steerState[eid] +=
    (steer - AiDriver.steerState[eid]) * Math.min(1, 12 * dt);
  Vehicle.steerInput[eid] = AiDriver.steerState[eid];

  // ---- Target speed from the corner it can see ----------------------------
  const brakingDistance = clamp(
    (speed * speed) / (2 * AI_BRAKE_PLANNING) + 12,
    18,
    140
  );
  const worstCurve = spline.maxCurvatureAhead(s, brakingDistance);
  const latBudget = AI_LATERAL_BUDGET * (0.72 + 0.28 * skill);
  const cornerSpeed =
    Math.abs(worstCurve) > 1e-4
      ? // Never plan to crawl: a hairpin whose curvature asks for 8 km/h turns
        // the AI into a rolling roadblock that circles the same corner forever.
        Math.max(MIN_CORNER_SPEED, Math.sqrt(latBudget / Math.abs(worstCurve)))
      : Number.POSITIVE_INFINITY;

  let targetSpeed = Math.min(
    (Vehicle.maxSpeed[eid] || 40) * (0.82 + 0.18 * skill),
    cornerSpeed
  );

  // Rubber band: close the gap to the player without ever being able to simply
  // drive away from them.
  const band = clamp(AiDriver.rubberBand[eid], 0, 1);
  if (band > 0 && player !== undefined) {
    const gap = playerDistance - RaceTracker.distance[eid]; // >0 = AI is behind
    const adjust = clamp(gap / 220, -0.16, 0.2) * band;
    targetSpeed *= 1 + adjust;
  }
  if (blocked) targetSpeed *= 0.94;

  // ---- Pedals -------------------------------------------------------------
  const err = targetSpeed - speed;
  if (err > 0.4) {
    Vehicle.throttle[eid] = clamp(err / 4, 0.35, 1);
    Vehicle.brakeInput[eid] = 0;
  } else if (err < -1.5) {
    Vehicle.throttle[eid] = 0;
    Vehicle.brakeInput[eid] = clamp(-err / 8, 0.25, 1);
  } else {
    Vehicle.throttle[eid] = 0.35;
    Vehicle.brakeInput[eid] = 0;
  }

  // Handbrake only to rotate the car when it is badly out of shape — the AI
  // should not be drifting for style, it should be making the apex.
  Vehicle.handbrake[eid] =
    Math.abs(headingErr) > 0.75 && Math.abs(speed) > 12 ? 1 : 0;

  // Boost down the straights when there is nothing to brake for.
  Vehicle.boostInput[eid] =
    Vehicle.boost[eid] > (Vehicle.boostCapacity[eid] || 0) * 0.35 &&
    Math.abs(worstCurve) < 0.006 &&
    speed > 12
      ? 1
      : 0;

  // ---- Stuck recovery -----------------------------------------------------
  // Measured as progress *along the track*, not as speed. A car wedged against
  // a barrier can be doing 10 m/s in a circle and never reach the next corner;
  // watching only the speedometer never notices.
  const progress = spline.deltaS(s, AiDriver.progressS[eid]);
  if (progress > 4) {
    AiDriver.progressS[eid] = s;
    AiDriver.stuckTimer[eid] = 0;
  } else {
    AiDriver.stuckTimer[eid] += dt;
  }

  if (AiDriver.stuckTimer[eid] > 2.5) {
    // Back out of whatever it is buried in, then aim straight back at the
    // centerline ahead rather than at the racing line it could not make.
    const backingUp = AiDriver.stuckTimer[eid] < 4;
    spline.positionAt(s + 25, 0, Vehicle.rideHeight[eid], _target);
    const rx = _target.x - Transform.posX[eid];
    const rz = _target.z - Transform.posZ[eid];
    const recoveryErr = wrapAngle(Math.atan2(rx, rz) - Vehicle.heading[eid]);
    Vehicle.handbrake[eid] = 0;
    Vehicle.boostInput[eid] = 0;
    if (backingUp) {
      Vehicle.throttle[eid] = 0;
      Vehicle.brakeInput[eid] = 1;
      Vehicle.steerInput[eid] = clamp(-recoveryErr * STEER_GAIN, -1, 1);
    } else {
      Vehicle.throttle[eid] = 1;
      Vehicle.brakeInput[eid] = 0;
      Vehicle.steerInput[eid] = clamp(recoveryErr * STEER_GAIN, -1, 1);
      if (AiDriver.stuckTimer[eid] > 6) {
        AiDriver.stuckTimer[eid] = 0;
        AiDriver.progressS[eid] = s;
      }
    }
  }
}
