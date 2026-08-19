import { defineSoundBank, preloadSounds } from 'vibegame';

/**
 * Single source of truth for every sound in the racer.
 *
 * Race-event SFX (countdown/go/lap/finish/nitro) are fired by the racing engine
 * plugin via bank keys `race-*` (guarded — silent no-op when undefined here).
 * BGM lives on the 'music' bus so the volume mixer controls it; loops are
 * gapless (generated with --seamless-loop, see scripts/gen-sfx.sh).
 */
export function registerGameSounds(): void {
  defineSoundBank({
    // ── Race events (2D, engine-fired) ─────────────────────────
    'race-countdown': { url: '/assets/audio/sfx_countdown.ogg', volume: 0.55 },
    'race-go': { url: '/assets/audio/sfx_go.ogg', volume: 0.7 },
    'race-lap': { url: '/assets/audio/sfx_lap.ogg', volume: 0.5 },
    'race-finish': { url: '/assets/audio/sfx_finish.ogg', volume: 0.6 },
    'race-nitro': { url: '/assets/audio/sfx_nitro.ogg', volume: 0.5 },
    'race-crash': { url: '/assets/audio/sfx_crash.ogg', volume: 0.55 },
    'race-respawn': { url: '/assets/audio/sfx_crash.ogg', volume: 0.45 },
    'race-pulse': { url: '/assets/audio/sfx_nitro.ogg', volume: 0.45 },
    'race-skid': { url: '/assets/audio/sfx_skid.ogg', volume: 0.4 },
    'race-engine-rev': {
      url: '/assets/audio/sfx_engine_rev.ogg',
      volume: 0.35,
    },
    'race-coin': { url: '/assets/audio/sfx_coin.ogg', volume: 0.45 },

    // ── Item game (chest → roulette → item → effect) ────────────
    'race-box': { url: '/assets/audio/sfx_coin.ogg', volume: 0.5, pitch: 0.85 },
    'race-roulette': {
      url: '/assets/audio/sfx_coin.ogg',
      volume: 0.45,
      pitch: 1.3,
    },
    'race-shield': { url: '/assets/audio/sfx_coin.ogg', volume: 0.4 },
    'race-trick': {
      url: '/assets/audio/sfx_nitro.ogg',
      volume: 0.45,
      pitch: 1.25,
    },
    'race-fireball': {
      url: '/assets/audio/sfx_nitro.ogg',
      volume: 0.55,
      pitch: 0.8,
    },
    'race-oil': {
      url: '/assets/audio/sfx_skid.ogg',
      volume: 0.45,
      pitch: 0.7,
    },
    'race-spin': {
      url: '/assets/audio/sfx_crash.ogg',
      volume: 0.6,
      pitch: 0.85,
    },

    // ── Save / load (same clips as simple-rpg) ─────────────────
    save: { url: '/assets/audio/sfx_save.ogg', volume: 0.48 },
    load: { url: '/assets/audio/sfx_load.ogg', volume: 0.44 },

    // ── Music (bus 'music', looped, 2D) ────────────────────────
    'bgm-race': {
      url: '/assets/audio/bgm_race.ogg',
      volume: 0.2,
      bus: 'music',
      loop: true,
    },
    'bgm-menu': {
      url: '/assets/audio/bgm_menu.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
  });
}

/** Preload bank clips (decode into Howl cache) without audible play/stop. */
export function preloadGameSounds(): void {
  preloadSounds([
    'race-countdown',
    'race-go',
    'race-lap',
    'race-finish',
    'race-nitro',
    'race-crash',
    'race-pulse',
    'race-respawn',
    'race-skid',
    'race-box',
    'race-roulette',
    'race-shield',
    'race-trick',
    'race-fireball',
    'race-oil',
    'race-spin',
    'save',
    'load',
    'bgm-race',
    'bgm-menu',
  ]);
}
