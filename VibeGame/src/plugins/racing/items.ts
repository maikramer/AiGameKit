import { defineSystem, defineQuery, type State, type System } from '../../core';
import { isKeyDown } from '../input';
import { Transform } from '../transforms';
import {
  HeldItem,
  ItemKind,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from './components';
import {
  addFireball,
  addOilSlick,
  getFireballs,
  getOilSlicks,
  getTrackSpline,
  removeFireball,
  removeOilSlick,
} from './data';
import type { TrackSpline } from './spline';
import { getSoundDef, playSound } from '../audio';
import { startSpinOut } from './tricks';
import { pushRacingBanner, pushRacingFx } from './fx-events';

/**
 * The single-slot item game: a position-weighted roulette, then one of four
 * medieval-flavoured items — Turbo (a speed potion), Fireball (homes onto the
 * kart ahead), Oil (a slick dropped behind) and Shield (eats one hit).
 *
 * Everything here runs in the fixed step *after* the vehicle controller, so
 * speed bumps and spin-outs land on the committed state and physics picks them
 * up on the next step.
 */

/** Seconds the collected box's roulette spins before it lands. */
export const ROULETTE_S = 1.1;
/** Turbo: how long the burst lasts (s). */
const TURBO_S = 1.6;
/** Turbo: acceleration while active (m/s²) and overspeed ceiling (× max). */
const TURBO_ASSIST = 11;
const TURBO_OVERSPEED = 1.32;
/** Shield: latch duration before it drops on its own (s). */
const SHIELD_S = 8;
/** Fireball: travel speed (m/s), homing range (m) and lifetime (s). */
const FIREBALL_SPEED = 52;
const FIREBALL_RANGE = 90;
const FIREBALL_TTL = 4.5;
/** Fireball: lateral homing rate (m/s) and hit window (m). */
const FIREBALL_HOMING = 9;
const FIREBALL_HIT = 1.6;
/** Oil: patch lifetime (s) and dropper immunity (s). */
const OIL_TTL = 12;
const OIL_OWNER_GRACE = 2;
/** Oil: drive-over window in arc length and lateral (m). */
const OIL_HIT = 1.8;

/** Names + icons for the HUD, indexed by ItemKind. */
export const ITEM_META = [
  { icon: '?', name: '', cls: '' },
  { icon: '🧪', name: 'Turbo', cls: 'turbo' },
  { icon: '🔥', name: 'Fireball', cls: 'fireball' },
  { icon: '🛢', name: 'Oil', cls: 'oil' },
  { icon: '🛡', name: 'Shield', cls: 'shield' },
] as const;

// Roulette weights [Turbo, Fireball, Oil, Shield]: the race leader gets
// defensive junk, the stragglers get the weapons that bring them back.
const LEADER_WEIGHTS = [1.2, 0.8, 3.0, 2.6];
const TRAILER_WEIGHTS = [4.0, 3.6, 0.8, 1.2];

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Roll an item for a kart sitting at `position` of `entrants`. Pure: inject
 * the random source for tests. Returns an ItemKind value (never None).
 */
export function rollItem(
  position: number,
  entrants: number,
  rand: () => number = Math.random
): number {
  const back =
    entrants > 1 ? clamp((position - 1) / (entrants - 1), 0, 1) : 0.5;
  let total = 0;
  const weights: number[] = [];
  for (let i = 0; i < 4; i++) {
    const w =
      LEADER_WEIGHTS[i]! + (TRAILER_WEIGHTS[i]! - LEADER_WEIGHTS[i]!) * back;
    weights.push(w);
    total += w;
  }
  let pick = rand() * total;
  for (let i = 0; i < 4; i++) {
    pick -= weights[i]!;
    if (pick <= 0) return i + 1;
  }
  return ItemKind.Turbo;
}

function playBanked(key: string): void {
  if (getSoundDef(key)) playSound(key);
}

function vehiclePos(eid: number): { x: number; y: number; z: number } {
  return {
    x: Transform.posX[eid] ?? 0,
    y: (Transform.posY[eid] ?? 0) + 1,
    z: Transform.posZ[eid] ?? 0,
  };
}

/**
 * Fire the held item (player key 1 and the AI both land here). Returns true
 * when something actually went off.
 */
export function useHeldItem(
  eid: number,
  spline: TrackSpline | undefined
): boolean {
  const item = HeldItem.item[eid] ?? ItemKind.None;
  if (item === ItemKind.None) return false;
  if ((HeldItem.rouletteTimer[eid] ?? 0) > 0) return false;

  if (item === ItemKind.Turbo) {
    HeldItem.item[eid] = ItemKind.None;
    HeldItem.turboTime[eid] = TURBO_S;
    playBanked('race-pulse');
    return true;
  }
  if (item === ItemKind.Fireball) {
    if (!spline) return false;
    HeldItem.item[eid] = ItemKind.None;
    addFireball(
      eid,
      spline.wrapS((Vehicle.trackS[eid] ?? 0) + 3),
      Vehicle.trackLateral[eid] ?? 0,
      FIREBALL_SPEED,
      FIREBALL_TTL
    );
    playBanked('race-fireball');
    return true;
  }
  if (item === ItemKind.Oil) {
    if (!spline) return false;
    HeldItem.item[eid] = ItemKind.None;
    const s = spline.wrapS((Vehicle.trackS[eid] ?? 0) - 4);
    const lateral = Vehicle.trackLateral[eid] ?? 0;
    addOilSlick(eid, s, lateral, OIL_TTL);
    const p = spline.positionAt(s, lateral);
    pushRacingFx({ kind: 'oil-drop', x: p.x, y: p.y + 0.4, z: p.z, eid });
    playBanked('race-oil');
    return true;
  }
  if (item === ItemKind.Shield) {
    if (HeldItem.shieldArmed[eid] === 1) return false;
    HeldItem.item[eid] = ItemKind.None;
    HeldItem.shieldArmed[eid] = 1;
    HeldItem.shieldTime[eid] = SHIELD_S;
    playBanked('race-shield');
    return true;
  }
  return false;
}

const itemQuery = defineQuery([HeldItem, Vehicle]);
const playerQuery = defineQuery([PlayerVehicle, HeldItem]);
const trackQuery = defineQuery([Track]);

let itemKeyHeld = false;

export const ItemSystem: System = defineSystem({
  name: 'ItemSystem',
  group: 'fixed',
  after: ['VehicleControlSystem'],

  update(state: State) {
    const dt = state.time.fixedDeltaTime;
    const entrants = itemQuery(state.world);
    if (entrants.length === 0) return;
    const trackEid = trackQuery(state.world)[0];
    const spline =
      trackEid !== undefined ? getTrackSpline(trackEid) : undefined;
    const player = playerQuery(state.world)[0];

    for (const eid of entrants) {
      // ---- Roulette ---------------------------------------------------------
      const roulette = HeldItem.rouletteTimer[eid] ?? 0;
      if (roulette > 0) {
        const next = roulette - dt;
        if (next <= 0) {
          HeldItem.rouletteTimer[eid] = 0;
          const rolled = rollItem(
            RaceTracker.position[eid] || 1,
            entrants.length
          );
          HeldItem.item[eid] = rolled;
          const p = vehiclePos(eid);
          pushRacingFx({ kind: 'roulette', x: p.x, y: p.y, z: p.z, eid });
          pushRacingBanner({
            eid,
            text: `${ITEM_META[rolled]!.icon} ${ITEM_META[rolled]!.name}!`,
            cls: 'item',
          });
        } else {
          HeldItem.rouletteTimer[eid] = next;
        }
      }

      // ---- Shield latch expiry ----------------------------------------------
      if (HeldItem.shieldArmed[eid] === 1) {
        const left = Math.max(0, (HeldItem.shieldTime[eid] ?? 0) - dt);
        HeldItem.shieldTime[eid] = left;
        if (left <= 0) HeldItem.shieldArmed[eid] = 0;
      }

      // ---- Turbo burst ------------------------------------------------------
      const turbo = HeldItem.turboTime[eid] ?? 0;
      if (turbo > 0) {
        HeldItem.turboTime[eid] = Math.max(0, turbo - dt);
        const max = (Vehicle.maxSpeed[eid] || 40) * TURBO_OVERSPEED;
        Vehicle.speed[eid] = Math.min(
          max,
          (Vehicle.speed[eid] ?? 0) + TURBO_ASSIST * dt
        );
        const capacity = Vehicle.boostCapacity[eid] || 0;
        if (capacity > 0) {
          Vehicle.boost[eid] = Math.max(
            Math.min(capacity, 1),
            Vehicle.boost[eid] ?? 0
          );
        }
        Vehicle.boosting[eid] = 1;
      }

      // ---- Player fire key --------------------------------------------------
      if (eid === player) {
        // J joins Digit1: the home row is the command cluster (J item, H horn,
        // K/L reserved), so firing never lifts a finger off WASD mid-corner —
        // which is what reaching for the number row cost.
        const held = isKeyDown('Digit1') || isKeyDown('KeyJ');
        if (held && !itemKeyHeld) useHeldItem(eid, spline);
        itemKeyHeld = held;
      }
    }

    if (spline) {
      updateFireballs(spline, entrants, dt);
      updateOilSlicks(spline, entrants, dt);
    }
  },

  dispose() {
    itemKeyHeld = false;
  },
});

/** Advance fireballs, home them in, and resolve hits. */
function updateFireballs(
  spline: TrackSpline,
  vehicles: readonly number[],
  dt: number
): void {
  const fireballs = getFireballs();
  for (let i = fireballs.length - 1; i >= 0; i--) {
    const fb = fireballs[i]!;
    fb.ttl -= dt;
    fb.s = spline.wrapS(fb.s + fb.speed * dt);

    // Home toward the nearest kart ahead inside the acquisition range.
    let target = -1;
    let bestGap = Infinity;
    for (const veh of vehicles) {
      const gap = spline.deltaS(Vehicle.trackS[veh] ?? 0, fb.s);
      if (gap <= FIREBALL_HIT || gap > FIREBALL_RANGE) continue;
      if (gap < bestGap) {
        bestGap = gap;
        target = veh;
      }
    }
    if (target >= 0) {
      const diff = (Vehicle.trackLateral[target] ?? 0) - fb.lateral;
      fb.lateral += clamp(diff, -FIREBALL_HOMING * dt, FIREBALL_HOMING * dt);
    }

    // Hit test: any kart inside the window (the dropper is safe only while
    // the fireball is young — after a wrap-around it is fair game).
    let victim = -1;
    for (const veh of vehicles) {
      if (veh === fb.ownerId && fb.ttl > FIREBALL_TTL - 0.6) continue;
      const gap = spline.deltaS(Vehicle.trackS[veh] ?? 0, fb.s);
      if (gap < -FIREBALL_HIT || gap > FIREBALL_HIT) continue;
      if (Math.abs((Vehicle.trackLateral[veh] ?? 0) - fb.lateral) > 1.7) {
        continue;
      }
      victim = veh;
      break;
    }
    const p = spline.positionAt(fb.s, fb.lateral);
    if (victim >= 0) {
      const spin = startSpinOut(victim);
      pushRacingFx({
        kind: spin === 'spun' ? 'fireball' : 'shield-block',
        x: p.x,
        y: p.y + 0.8,
        z: p.z,
        severity: 1,
        eid: victim,
      });
      if (spin === 'spun') {
        pushRacingBanner({ eid: victim, text: 'SPUN OUT!', cls: 'spin' });
      }
      removeFireball(i);
      continue;
    }
    if (fb.ttl <= 0) {
      pushRacingFx({
        kind: 'fizzle',
        x: p.x,
        y: p.y + 0.6,
        z: p.z,
        severity: 0.4,
      });
      removeFireball(i);
    }
  }
}

/** Age oil slicks out and spin whoever drives over one. */
function updateOilSlicks(
  spline: TrackSpline,
  vehicles: readonly number[],
  dt: number
): void {
  const slicks = getOilSlicks();
  for (let i = slicks.length - 1; i >= 0; i--) {
    const slick = slicks[i]!;
    slick.ttl -= dt;
    if (slick.ttl <= 0) {
      removeOilSlick(i);
      continue;
    }
    for (const veh of vehicles) {
      if (veh === slick.ownerId && slick.ttl > OIL_TTL - OIL_OWNER_GRACE) {
        continue;
      }
      if (Vehicle.airborne[veh] === 1) continue;
      const gap = spline.deltaS(Vehicle.trackS[veh] ?? 0, slick.s);
      if (gap < -OIL_HIT || gap > OIL_HIT) continue;
      if (
        Math.abs((Vehicle.trackLateral[veh] ?? 0) - slick.lateral) > OIL_HIT
      ) {
        continue;
      }
      const spin = startSpinOut(veh, 1.0);
      const p = spline.positionAt(slick.s, slick.lateral);
      pushRacingFx({
        kind: spin === 'spun' ? 'oil-hit' : 'shield-block',
        x: p.x,
        y: p.y + 0.4,
        z: p.z,
        severity: 0.6,
        eid: veh,
      });
      if (spin === 'spun') {
        pushRacingBanner({ eid: veh, text: 'SPUN OUT!', cls: 'spin' });
      }
      removeOilSlick(i);
      break;
    }
  }
}
