import { defineSoundBank, preloadSounds } from 'vibegame';

/**
 * Single source of truth for every sound in the game.
 *
 * World SFX (hurt/death/harvest/explosions) are ``spatial: true`` and must be
 * fired with ``playSoundAt`` / ``playSoundOn`` so Howler attenuates by distance
 * and the bank culls past ``maxDistance``. UI / player-local SFX stay 2D.
 */
export function registerGameSounds(): void {
  const worldSfx = {
    spatial: true as const,
    minDistance: 2,
    maxDistance: 36,
    rolloff: 1.2,
  };

  defineSoundBank({
    // ── UI / player-local (2D) ─────────────────────────────────────────
    save: { url: '/assets/audio/sfx_save.ogg', volume: 0.48 },
    load: { url: '/assets/audio/sfx_load.ogg', volume: 0.44 },
    heal: { url: '/assets/audio/sfx_heal.ogg', volume: 0.48 },
    'shop-open': { url: '/assets/audio/sfx_shop_open.ogg', volume: 0.45 },
    buy: { url: '/assets/audio/sfx_buy.ogg', volume: 0.45 },
    error: { url: '/assets/audio/sfx_error.ogg', volume: 0.4 },
    'player-hurt': { url: '/assets/audio/sfx_player_hurt.ogg', volume: 0.5 },
    coin: { url: '/assets/audio/sfx_coin.ogg', volume: 0.42 },
    levelup: { url: '/assets/audio/sfx_levelup.ogg', volume: 0.55 },
    swing: { url: '/assets/audio/sfx_swing.ogg', volume: 0.3 },

    // ── World SFX (spatial — use playSoundAt) ────────────────────────
    'bomb-drop': {
      url: '/assets/audio/sfx_bomb_drop.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'enemy-hurt': {
      url: '/assets/audio/sfx_enemy_hurt.ogg',
      volume: 0.42,
      ...worldSfx,
    },
    'enemy-death': {
      url: '/assets/audio/sfx_enemy_death.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'boss-roar': {
      url: '/assets/audio/sfx_boss_roar.ogg',
      volume: 0.55,
      ...worldSfx,
      maxDistance: 48,
    },
    'item-drop': {
      url: '/assets/audio/sfx_item_drop.ogg',
      volume: 0.4,
      ...worldSfx,
    },
    'mine-hit': {
      url: '/assets/audio/sfx_mine_hit.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'chop-hit': {
      url: '/assets/audio/sfx_chop_hit.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'mine-break': {
      url: '/assets/audio/sfx_mine_break.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'chop-break': {
      url: '/assets/audio/sfx_chop_break.ogg',
      volume: 0.5,
      ...worldSfx,
    },

    // ── Music (bus 'music', looped, 2D) ──────────────────────────────
    'bgm-battle': {
      url: '/assets/audio/bgm_battle.ogg',
      volume: 0.22,
      bus: 'music',
      loop: true,
    },
    'bgm-explore': {
      url: '/assets/audio/bgm_explore.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
  });
}

/**
 * Preload bank clips (decode into Howl cache) without audible play/stop.
 * Spatial defs warm the spatial Howl used by ``playSoundAt``.
 */
export function preloadGameSounds(): void {
  preloadSounds([
    'save',
    'load',
    'bomb-drop',
    'heal',
    'enemy-hurt',
    'enemy-death',
    'boss-roar',
    'shop-open',
    'buy',
    'error',
    'player-hurt',
    'coin',
    'item-drop',
    'mine-hit',
    'chop-hit',
    'mine-break',
    'chop-break',
    'levelup',
    'swing',
  ]);
}
