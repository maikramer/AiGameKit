import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { Transform } from '../transforms';
import { HeldItem, PlayerVehicle, Vehicle } from './components';
import { pushRacingBanner, pushRacingFx } from './fx-events';

/**
 * Mid-air stunts (Mario-Kart style) and the spin-out state every hazard feeds
 * into. A stunt is started with Space while airborne — steer picks the roll
 * direction, brake turns it into a front flip, no input does a 360 — and pays
 * out on landing: a full rotation earns nitro, a half-hearted one earns a
 * face-plant.
 */

/** Stunt kinds; keep in sync with the `trickKind` component doc. */
export const TrickKind = {
  None: 0,
  RollLeft: 1,
  RollRight: 2,
  FrontFlip: 3,
  Spin360: 4,
} as const;

const TRICK_TARGET = Math.PI * 2;
/** A full rotation completes in this many seconds. */
const TRICK_SPIN_RATE = (Math.PI * 2) / 0.75;
/** Nitro seconds a clean stunt pays into the tank. */
const TRICK_BOOST_S = 1.0;
/** Speed a clean stunt pays when the kart has no nitro tank (m/s). */
const TRICK_SPEED_BUMP = 6;
/** Default spin-out duration (s). */
const SPIN_OUT_S = 1.15;

const TRICK_LABELS = [
  '',
  'BARREL ROLL!',
  'BARREL ROLL!',
  'FRONT FLIP!',
  '360!',
];

export type SpinResult = 'spun' | 'blocked' | 'immune';

/**
 * Start a stunt. Only valid in the air, one at a time, never mid-spin-out.
 * The AI calls this directly; the player goes through the Space key here.
 */
export function startTrick(eid: number, kind: number): boolean {
  if (Vehicle.airborne[eid] !== 1) return false;
  if (Vehicle.trickActive[eid] === 1) return false;
  if ((Vehicle.spinOutTimer[eid] ?? 0) > 0) return false;
  Vehicle.trickKind[eid] = kind;
  Vehicle.trickSpin[eid] = 0;
  Vehicle.trickActive[eid] = 1;
  return true;
}

/**
 * Throw a vehicle into a spin-out. A latched shield eats the hit instead.
 * Returns what happened so callers can play the right FX.
 */
export function startSpinOut(eid: number, duration = SPIN_OUT_S): SpinResult {
  if ((Vehicle.spinOutTimer[eid] ?? 0) > 0) return 'immune';
  if (HeldItem.shieldArmed[eid] === 1) {
    HeldItem.shieldArmed[eid] = 0;
    HeldItem.shieldTime[eid] = 0;
    return 'blocked';
  }
  Vehicle.spinOutTimer[eid] = duration;
  Vehicle.spinOutTotal[eid] = duration;
  Vehicle.trickActive[eid] = 0;
  Vehicle.trickKind[eid] = 0;
  Vehicle.trickSpin[eid] = 0;
  return 'spun';
}

const vehicleQuery = defineQuery([Vehicle]);
const playerQuery = defineQuery([PlayerVehicle, Vehicle]);

const prevAirborne = new Map<number, number>();
const spaceHeld = new Map<number, boolean>();

export const TrickSystem: System = defineSystem({
  name: 'TrickSystem',
  group: 'fixed',
  after: ['VehicleControlSystem'],

  update(state: State) {
    const dt = state.time.fixedDeltaTime;
    const player = playerQuery(state.world)[0];
    const vehicles = vehicleQuery(state.world);

    for (const eid of vehicles) {
      const airborne = Vehicle.airborne[eid] === 1;

      // Player: Space starts a stunt while airborne (on the ground it is the
      // handbrake, so the same key does double duty without a conflict).
      if (eid === player && airborne) {
        const held = isKeyDown('Space');
        const wasHeld = spaceHeld.get(eid) ?? false;
        if (held && !wasHeld && Vehicle.trickActive[eid] !== 1) {
          const steer = Vehicle.steerInput[eid] ?? 0;
          const kind =
            (Vehicle.brakeInput[eid] ?? 0) > 0.5
              ? TrickKind.FrontFlip
              : Math.abs(steer) > 0.35
                ? steer > 0
                  ? TrickKind.RollLeft
                  : TrickKind.RollRight
                : TrickKind.Spin360;
          startTrick(eid, kind);
        }
        spaceHeld.set(eid, held);
      }

      if (Vehicle.trickActive[eid] === 1 && airborne) {
        Vehicle.trickSpin[eid] = Math.min(
          TRICK_TARGET,
          (Vehicle.trickSpin[eid] ?? 0) + TRICK_SPIN_RATE * dt
        );
      }

      // Landing edge: pay or punish the stunt.
      if (prevAirborne.get(eid) === 1 && !airborne) {
        resolveLanding(eid);
      }
      prevAirborne.set(eid, airborne ? 1 : 0);
    }
  },

  dispose() {
    prevAirborne.clear();
    spaceHeld.clear();
  },
});

function resolveLanding(eid: number): void {
  if (Vehicle.trickActive[eid] !== 1) return;
  const spin = Vehicle.trickSpin[eid] ?? 0;
  const kind = Vehicle.trickKind[eid] ?? 0;
  const complete = spin >= TRICK_TARGET - 0.02;
  Vehicle.trickActive[eid] = 0;
  Vehicle.trickKind[eid] = 0;
  Vehicle.trickSpin[eid] = 0;

  const x = Transform.posX[eid] ?? 0;
  const y = (Transform.posY[eid] ?? 0) + 1.4;
  const z = Transform.posZ[eid] ?? 0;

  if (complete) {
    const capacity = Vehicle.boostCapacity[eid] || 0;
    if (capacity > 0) {
      Vehicle.boost[eid] = Math.min(
        capacity,
        (Vehicle.boost[eid] ?? 0) + TRICK_BOOST_S
      );
    } else {
      Vehicle.speed[eid] = Math.min(
        (Vehicle.maxSpeed[eid] || 40) * 1.12,
        (Vehicle.speed[eid] ?? 0) + TRICK_SPEED_BUMP
      );
    }
    pushRacingFx({ kind: 'trick', x, y, z, eid });
    pushRacingBanner({
      eid,
      text: TRICK_LABELS[kind] ?? 'NICE!',
      cls: 'trick',
    });
  } else {
    // Bailed out mid-rotation: the landing already cost speed, the tumble
    // costs a little more so a botched trick is never the fast line.
    Vehicle.speed[eid] = (Vehicle.speed[eid] ?? 0) * 0.86;
    Vehicle.impactTimer[eid] = 0;
    pushRacingFx({ kind: 'trick-fail', x, y, z, eid });
  }
}
