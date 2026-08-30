import { defineSoundBank, preloadSounds } from 'aigamekit-vibegame';

/**
 * Single source of truth for every sound in the racer.
 *
 * Todos os clips vêm do pool partilhado (`examples/shared-assets/public/assets/audio`)
 * via plugin vibegame({ sharedAssets }) — nenhum binário local. Um jogo pode
 * sobrepor um clip largando um ficheiro com o mesmo caminho no seu `public/`.
 *
 * Race-event SFX (countdown/go/lap/finish/nitro) are fired by the racing engine
 * plugin via bank keys `race-*` (guarded — silent no-op when undefined here).
 * BGM lives on the 'music' bus so the volume mixer controls it; loops are
 * gapless (pipeline seamless do Text2Sound — crossfade equal-power + fold).
 */
export function registerGameSounds(): void {
  defineSoundBank({
    // ── Race events (2D, engine-fired) ─────────────────────────
    'race-countdown': {
      url: '/assets/audio/sfx/ui/countdown.ogg',
      volume: 0.55,
    },
    'race-go': { url: '/assets/audio/sfx/ui/go.ogg', volume: 0.7 },
    'race-lap': { url: '/assets/audio/sfx/ui/lap.ogg', volume: 0.5 },
    'race-finish': { url: '/assets/audio/sfx/ui/finish.ogg', volume: 0.6 },
    'race-nitro': { url: '/assets/audio/sfx/vehicles/nitro.ogg', volume: 0.5 },
    'race-crash': { url: '/assets/audio/sfx/vehicles/crash.ogg', volume: 0.55 },
    'race-respawn': {
      url: '/assets/audio/sfx/vehicles/crash.ogg',
      volume: 0.45,
    },
    'race-pulse': { url: '/assets/audio/sfx/vehicles/nitro.ogg', volume: 0.45 },
    'race-skid': { url: '/assets/audio/sfx/vehicles/skid.ogg', volume: 0.4 },
    'race-engine-rev': {
      url: '/assets/audio/sfx/vehicles/engine_rev.ogg',
      volume: 0.35,
    },
    'race-horn': { url: '/assets/audio/sfx/vehicles/horn.ogg', volume: 0.5 },
    'race-coin': { url: '/assets/audio/sfx/ui/coin.ogg', volume: 0.45 },

    // ── Item game (chest → roulette → item → effect) ────────────
    'race-box': {
      url: '/assets/audio/sfx/ui/coin.ogg',
      volume: 0.5,
      pitch: 0.85,
    },
    'race-roulette': {
      url: '/assets/audio/sfx/ui/coin.ogg',
      volume: 0.45,
      pitch: 1.3,
    },
    'race-shield': { url: '/assets/audio/sfx/ui/coin.ogg', volume: 0.4 },
    'race-trick': {
      url: '/assets/audio/sfx/vehicles/nitro.ogg',
      volume: 0.45,
      pitch: 1.25,
    },
    'race-drift': {
      url: '/assets/audio/sfx/vehicles/nitro.ogg',
      volume: 0.5,
      pitch: 1.4,
    },
    'race-fireball': {
      url: '/assets/audio/sfx/vehicles/nitro.ogg',
      volume: 0.55,
      pitch: 0.8,
    },
    'race-oil': {
      url: '/assets/audio/sfx/vehicles/skid.ogg',
      volume: 0.45,
      pitch: 0.7,
    },
    'race-spin': {
      url: '/assets/audio/sfx/vehicles/crash.ogg',
      volume: 0.6,
      pitch: 0.85,
    },

    // ── Save / load (clips partilhados do pool) ─────────────────
    save: { url: '/assets/audio/sfx/ui/save.ogg', volume: 0.48 },
    load: { url: '/assets/audio/sfx/ui/load.ogg', volume: 0.44 },

    // ── Music (bus 'music', looped, 2D) ────────────────────────
    'bgm-race': {
      url: '/assets/audio/bgm/race.ogg',
      volume: 0.2,
      bus: 'music',
      loop: true,
    },
    'bgm-menu': {
      url: '/assets/audio/bgm/menu.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
    // Temas do pool disponíveis para menus alternativos (lazy — sem preload)
    'bgm-battle': {
      url: '/assets/audio/bgm/battle.ogg',
      volume: 0.2,
      bus: 'music',
      loop: true,
    },
    'bgm-credits': {
      url: '/assets/audio/bgm/credits.ogg',
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
    'race-drift',
    'race-fireball',
    'race-oil',
    'race-spin',
    'save',
    'load',
    'bgm-race',
    'bgm-menu',
  ]);
}
