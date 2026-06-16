import { addClipSound, defineSoundBank } from 'vibegame';

/**
 * Single source of truth for every sound in the game.
 *
 * To add a sound: add one line here, then call `playSound('key')` anywhere —
 * no scene entity, no eid lookup, no per-frame wiring. SFX route through the
 * 'sfx' bus by default; music through 'music' (see pause-menu volume sliders).
 */
export function registerGameSounds(): void {
  defineSoundBank({
    // ── SFX (bus 'sfx') ──────────────────────────────────────────────
    jump: { url: '/assets/audio/sfx_jump.wav', volume: 0.42 },
    save: { url: '/assets/audio/sfx_save.wav', volume: 0.48 },
    load: { url: '/assets/audio/sfx_load.wav', volume: 0.44 },
    heal: { url: '/assets/audio/sfx_heal.ogg', volume: 0.48 },
    hit: { url: '/assets/audio/sfx_hit.ogg', volume: 0.45 },
    'enemy-hurt': { url: '/assets/audio/sfx_enemy_hurt.ogg', volume: 0.42 },
    'enemy-death': { url: '/assets/audio/sfx_enemy_death.ogg', volume: 0.5 },
    'boss-roar': { url: '/assets/audio/sfx_boss_roar.ogg', volume: 0.55 },
    'shop-open': { url: '/assets/audio/sfx_shop_open.ogg', volume: 0.45 },
    buy: { url: '/assets/audio/sfx_buy.ogg', volume: 0.45 },
    error: { url: '/assets/audio/sfx_error.ogg', volume: 0.4 },
    'player-hurt': { url: '/assets/audio/sfx_player_hurt.ogg', volume: 0.5 },
    coin: { url: '/assets/audio/sfx_coin.ogg', volume: 0.42 },
    'item-drop': { url: '/assets/audio/sfx_item_drop.ogg', volume: 0.4 },
    'mine-hit': { url: '/assets/audio/sfx_mine_hit.ogg', volume: 0.45 },
    'chop-hit': { url: '/assets/audio/sfx_chop_hit.ogg', volume: 0.45 },
    'mine-break': { url: '/assets/audio/sfx_mine_break.ogg', volume: 0.5 },
    'chop-break': { url: '/assets/audio/sfx_chop_break.ogg', volume: 0.5 },
    levelup: { url: '/assets/audio/sfx_levelup.ogg', volume: 0.55 },
    swing: { url: '/assets/audio/sfx_swing.ogg', volume: 0.3 },

    // ── Music (bus 'music', looped) ──────────────────────────────────
    'bgm-field': {
      url: '/assets/audio/bgm_field.wav',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
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

  // Example of an animation-pinned sound: the attack whoosh fires from the
  // swing clip itself rather than from input-handling code. (Kept commented
  // because the current swing SFX is driven by the input edge in main.ts.)
  // addClipSound('Animator3D_Attack', { at: 0.25, sound: 'swing' });
  void addClipSound;
}
