import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import {
  PlayerVehicle,
  PowerUp,
  RaceTracker,
  TrackObstacleState,
  Vehicle,
} from './components';
import {
  getTrackSpline,
  getTrackSpaceObstacles,
  repositionTrackObstacle,
} from './data';
import type { TrackSpline } from './spline';
import { getSoundDef, playSound } from '../audio';

/**
 * Cooldown duration for each slot (s). The HUD uses this to drive the radial
 * cooldown overlay.
 */
const COOLDOWN_S = [0.6, 2.2, 1.4];
/** Max ammo per slot when fully loaded. */
const DEFAULT_AMMO = [1, 1, 1];
/** How long Pulse's +X boost sticks around (s). */
const PULSE_BOOST_S = 1.2;
/** Sidewinder detection range (m ahead of the car on the track). */
const SIDEWINDER_RANGE = 35;
/** How long the shield stays armed before dropping the latch (s). */
const SHIELD_LATCH_S = 6;
/** Distance a sidewinder pushes the obstacle along the track (m). */
const SIDEWINDER_PUSH_S = 4.5;
/** How long a sidewinder bolt stays visible (ms). */
const SIDEWINDER_BOLT_MS = 420;

export interface SidewinderBolt {
  ax: number;
  ay: number;
  az: number;
  bx: number;
  by: number;
  bz: number;
  born: number;
}

const bolts: SidewinderBolt[] = [];

function pruneBolts(now: number): void {
  for (let i = bolts.length - 1; i >= 0; i--) {
    if (now - bolts[i]!.born > SIDEWINDER_BOLT_MS) bolts.splice(i, 1);
  }
}

/** Live sidewinder flashes (obstacle visual system draws them). */
export function getSidewinderBolts(
  now = performance.now()
): readonly SidewinderBolt[] {
  pruneBolts(now);
  return bolts;
}

export function resetSidewinderBolts(): void {
  bolts.length = 0;
}

function playBanked(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

const playerQuery = defineQuery([PlayerVehicle, PowerUp]);
const powerupQuery = defineQuery([PowerUp, Vehicle]);
const trackQuery = defineQuery([RaceTracker]);

/** Edge trigger so holding 1/2/3 does not dump the whole magazine. */
const puHeld = [false, false, false];

/** Sidewinder finds the nearest obstacle ahead of the car and shoves it. */
function sidewinderHit(
  spline: TrackSpline,
  carS: number,
  carLateral: number
): number {
  const obbs = getTrackSpaceObstacles();
  if (obbs.length === 0) return -1;
  let best = -1;
  let bestDist = Infinity;
  for (let i = 0; i < obbs.length; i++) {
    const o = obbs[i];
    if (!o) continue;
    const ds = spline.deltaS(o.s, carS);
    if (ds <= 0 || ds > SIDEWINDER_RANGE) continue;
    const lateral = Math.abs(o.lateral - carLateral);
    // Prefer obstacles in roughly the same lane; far-off obstacles need not be
    // displaceable — pushing a kerb sign into the asphalt reads as a glitch.
    if (lateral > 4) continue;
    const score = ds + lateral * 0.5;
    if (score < bestDist) {
      bestDist = score;
      best = i;
    }
  }
  return best;
}

/**
 * Power-up inventory + activation.
 *
 * Ammo is loaded by the parser (`loadout`) and topped up by the pickup system
 * for *any* vehicle — the AI collects orbs too. This system ticks cooldowns
 * and effects for every powered vehicle each fixed step, and polls the
 * activation keys for the player only. The AI drives its own slots through
 * {@link usePowerUpSlot}.
 */
export const PowerUpSystem: System = defineSystem({
  name: 'PowerUpSystem',
  group: 'fixed',
  before: ['VehicleControlSystem'],

  update(state: State) {
    const dt = state.time.fixedDeltaTime;
    const powered = powerupQuery(state.world);
    if (powered.length === 0) return;
    const trackEid = trackQuery(state.world)[0];
    const spline =
      trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
    const player = playerQuery(state.world)[0];

    for (const eid of powered) {
      // ---- Tick existing effects -----------------------------------------
      if (PowerUp.pulseBoost[eid]! > 0) {
        PowerUp.pulseBoost[eid] = Math.max(0, PowerUp.pulseBoost[eid]! - dt);
      }
      for (let i = 0; i < 3; i++) {
        const cd =
          i === 0
            ? PowerUp.cd0[eid]
            : i === 1
              ? PowerUp.cd1[eid]
              : PowerUp.cd2[eid];
        if ((cd ?? 0) > 0) {
          // Cooldowns tick down here. Ammo does NOT auto-recharge: the pickups
          // on the track are the only way to refill a slot, so collecting an
          // orb always matters.
          const next = Math.max(0, (cd ?? 0) - dt);
          if (i === 0) PowerUp.cd0[eid] = next;
          else if (i === 1) PowerUp.cd1[eid] = next;
          else PowerUp.cd2[eid] = next;
        }
      }

      // ---- Player keys (1/2/3 only — W is throttle, Q is pause) ----------
      if (eid === player) {
        const keys = [
          isKeyDown('Digit1'),
          isKeyDown('Digit2'),
          isKeyDown('Digit3'),
        ];
        for (let i = 0; i < 3; i++) {
          if (keys[i] && !puHeld[i]) usePowerUpSlot(eid, i, spline);
          puHeld[i] = keys[i] ?? false;
        }
      }

      // Pulse boost stacks on top of the vehicle's own boost accumulator as a
      // temporary additive (read by the controller).
      if ((PowerUp.pulseBoost[eid] ?? 0) > 0) {
        Vehicle.boost[eid] = Math.max(
          Vehicle.boost[eid] ?? 0,
          (PowerUp.pulseBoost[eid] ?? 0) * 10
        );
      }
    }
  },

  dispose() {
    resetPowerUpDefaults();
  },
});

/**
 * Fire a power-up slot for a vehicle (player keys or AI decision both land
 * here). Returns true when the slot actually fired.
 *
 * - 0 Pulse: instant +boost for a short burst.
 * - 1 Sidewinder: shoves the nearest obstacle ahead of the car along the track.
 * - 2 Shield: arms the respawn shield for a few seconds.
 */
export function usePowerUpSlot(
  eid: number,
  slot: number,
  spline: TrackSpline | undefined
): boolean {
  if (slot === 0) {
    if ((PowerUp.pulseBoost[eid] ?? 0) > 0 || (PowerUp.ammo0[eid] ?? 0) <= 0)
      return false;
    PowerUp.ammo0[eid] = Math.max(0, (PowerUp.ammo0[eid] ?? 0) - 1);
    PowerUp.pulseBoost[eid] = PULSE_BOOST_S;
    PowerUp.cd0[eid] = PowerUp.cdTotal0[eid] || COOLDOWN_S[0]!;
    playBanked('race-pulse');
    return true;
  }
  if (slot === 1) {
    if (
      (PowerUp.cd1[eid] ?? 0) > 0 ||
      (PowerUp.ammo1[eid] ?? 0) <= 0 ||
      !spline
    )
      return false;
    const hit = sidewinderHit(
      spline,
      Vehicle.trackS[eid],
      Vehicle.trackLateral[eid]
    );
    if (hit < 0) return false;
    PowerUp.ammo1[eid] = Math.max(0, (PowerUp.ammo1[eid] ?? 0) - 1);
    PowerUp.cd1[eid] = PowerUp.cdTotal1[eid] || COOLDOWN_S[1]!;
    // Apply the push: scratch the obstacle's track position; the visual
    // system + sidewinder-helper pick this up next step.
    const o = getTrackSpaceObstacles()[hit];
    if (o) {
      const from = spline.positionAt(
        Vehicle.trackS[eid],
        Vehicle.trackLateral[eid],
        0.7
      );
      const to = spline.positionAt(o.s, o.lateral, 0.9);
      bolts.push({
        ax: from.x,
        ay: from.y,
        az: from.z,
        bx: to.x,
        by: to.y,
        bz: to.z,
        born: performance.now(),
      });
      o.s = spline.wrapS(o.s + SIDEWINDER_PUSH_S);
      const shoved = spline.positionAt(o.s, o.lateral);
      repositionTrackObstacle(hit, shoved.x, shoved.z);
      if (o.eid >= 0) TrackObstacleState.s[o.eid] = o.s;
    }
    playBanked('race-sidewinder');
    return true;
  }
  if (slot === 2) {
    if ((PowerUp.shieldArmed[eid] ?? 0) !== 0 || (PowerUp.ammo2[eid] ?? 0) <= 0)
      return false;
    PowerUp.ammo2[eid] = Math.max(0, (PowerUp.ammo2[eid] ?? 0) - 1);
    PowerUp.shieldArmed[eid] = 1;
    PowerUp.cd2[eid] = SHIELD_LATCH_S;
    playBanked('race-shield');
    return true;
  }
  return false;
}

/** Reset state when the plugin is disposed. */
export function resetPowerUpDefaults(): void {
  puHeld[0] = puHeld[1] = puHeld[2] = false;
  resetSidewinderBolts();
}

/** Add one ammo to a slot (used by the pickup system). */
export function grantPowerUpAmmo(eid: number, slot: number, amount = 1): void {
  if (slot < 0 || slot > 2) return;
  const cap =
    slot === 0
      ? PowerUp.cap0[eid] || DEFAULT_AMMO[0]!
      : slot === 1
        ? PowerUp.cap1[eid] || DEFAULT_AMMO[1]!
        : PowerUp.cap2[eid] || DEFAULT_AMMO[2]!;
  const arr =
    slot === 0 ? PowerUp.ammo0 : slot === 1 ? PowerUp.ammo1 : PowerUp.ammo2;
  arr[eid] = Math.min(cap, (arr[eid] ?? 0) + amount);
}
