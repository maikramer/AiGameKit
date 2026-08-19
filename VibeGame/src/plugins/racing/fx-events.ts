import { defineSystem, defineQuery, type State, type System } from '../../core';
import { spawnParticleBurst } from '../particles/utils';
import { getSoundDef, playSound } from '../audio';
import { PlayerVehicle } from './components';

/**
 * One-shot FX events raised by the fixed-step simulation (car contacts, crate
 * breaks, item box grabs, fireball hits, stunts) and played out at draw time —
 * particles, banked SFX and the impact shake the chase camera reads.
 *
 * The queue is the whole integration: sim code calls {@link pushRacingFx} and
 * never touches THREE or the audio graph itself.
 */

export type RacingFxKind =
  | 'bump' // sparks + thud — light car-to-car contact
  | 'spin' // heavy contact: sparks + crash + camera shake
  | 'crate' // a breakable crate shatters into woodchips
  | 'box' // an item box is cracked open
  | 'roulette' // the collected box's roulette stops
  | 'fireball' // a fireball finds its target
  | 'fizzle' // a fireball dies of old age
  | 'oil-drop' // an oil patch hits the tarmac
  | 'oil-hit' // someone drove over an oil patch
  | 'trick' // a stunt landed clean
  | 'trick-fail' // a stunt landed badly
  | 'shield-block'; // a latched shield ate a hit

export interface RacingFxEvent {
  kind: RacingFxKind;
  x: number;
  y: number;
  z: number;
  /** 0..1 — sizes the burst and the camera shake. */
  severity?: number;
  /** The vehicle the event happened *to* (player check for the shake). */
  eid?: number;
}

const queue: RacingFxEvent[] = [];

export function pushRacingFx(event: RacingFxEvent): void {
  if (queue.length < 32) queue.push(event);
}

// ---- HUD banners --------------------------------------------------------------

/** Short-lived centre-screen shout (stunt landed, spun out, item rolled). */
export interface RacingBannerEvent {
  eid: number;
  text: string;
  cls: 'trick' | 'spin' | 'item';
}

const banners: RacingBannerEvent[] = [];

export function pushRacingBanner(event: RacingBannerEvent): void {
  if (banners.length < 8) banners.push(event);
}

/** Take (and clear) the pending banners. The HUD calls this every frame. */
export function drainRacingBanners(): RacingBannerEvent[] {
  return banners.splice(0, banners.length);
}

export function resetRacingFx(): void {
  queue.length = 0;
  banners.length = 0;
  shakeLevel = 0;
}

// ---- Impact shake ------------------------------------------------------------

let shakeLevel = 0;

/** Ratchet the impact shake up (clamped); the camera reads it every frame. */
export function addImpactShake(amount: number): void {
  shakeLevel = Math.min(1, shakeLevel + amount);
}

/** Current shake level 0..1 (decays in {@link RacingFxSystem}). */
export function getImpactShake(): number {
  return shakeLevel;
}

// ---- The draw-time player ----------------------------------------------------

const BURST_PRESET: Partial<Record<RacingFxKind, string>> = {
  bump: 'sparks',
  spin: 'sparks',
  crate: 'woodchips',
  box: 'magic',
  fireball: 'explosion',
  fizzle: 'smoke',
  'oil-drop': 'splash',
  'oil-hit': 'splash',
  trick: 'magic',
  'trick-fail': 'dust',
  'shield-block': 'magic',
};

const SOUND_KEY: Partial<Record<RacingFxKind, string>> = {
  bump: 'race-crash',
  spin: 'race-spin',
  crate: 'race-crash',
  box: 'race-box',
  roulette: 'race-roulette',
  fireball: 'race-fireball',
  'oil-drop': 'race-oil',
  'oil-hit': 'race-oil',
  trick: 'race-trick',
  'trick-fail': 'race-crash',
  'shield-block': 'race-shield',
};

const playerQuery = defineQuery([PlayerVehicle]);

export const RacingFxSystem: System = defineSystem({
  name: 'RacingFxSystem',
  group: 'draw',

  update(state: State) {
    // The shake decays on wall time so it reads the same at any frame rate.
    shakeLevel *= Math.max(0, 1 - 4.5 * state.time.deltaTime);
    if (shakeLevel < 0.01) shakeLevel = 0;
    if (queue.length === 0) return;
    if (state.headless) {
      queue.length = 0;
      return;
    }
    const player = playerQuery(state.world)[0];
    for (const e of queue.splice(0, queue.length)) {
      const preset = BURST_PRESET[e.kind];
      if (preset) {
        const severity = Math.max(0.35, Math.min(1, e.severity ?? 0.6));
        spawnParticleBurst(state, {
          x: e.x,
          y: e.y,
          z: e.z,
          preset,
          count: Math.round(8 + severity * 26),
          duration: 0.9,
        });
      }
      const key = SOUND_KEY[e.kind];
      if (key && getSoundDef(key)) playSound(key);
      if (
        (e.kind === 'bump' || e.kind === 'spin') &&
        e.eid !== undefined &&
        e.eid === player
      ) {
        addImpactShake(
          (e.kind === 'spin' ? 0.55 : 0.25) * Math.max(0.4, e.severity ?? 0.5)
        );
      }
    }
  },

  dispose() {
    resetRacingFx();
  },
});
