import { defineSoundBank, preloadSounds } from 'vibegame';

/**
 * Single source of truth for every sound in the game.
 *
 * Todos os clips vêm do pool partilhado (`examples/shared-assets/public/assets/audio`)
 * via plugin vibegame({ sharedAssets }) — nenhum binário local. Um jogo pode
 * sobrepor um clip largando um ficheiro com o mesmo caminho no seu `public/`.
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
    save: { url: '/assets/audio/sfx/ui/save.ogg', volume: 0.48 },
    load: { url: '/assets/audio/sfx/ui/load.ogg', volume: 0.44 },
    heal: { url: '/assets/audio/sfx/player/heal.ogg', volume: 0.48 },
    'shop-open': { url: '/assets/audio/sfx/ui/shop_open.ogg', volume: 0.45 },
    buy: { url: '/assets/audio/sfx/ui/buy.ogg', volume: 0.45 },
    error: { url: '/assets/audio/sfx/ui/error.ogg', volume: 0.4 },
    'player-hurt': { url: '/assets/audio/sfx/player/hurt.ogg', volume: 0.5 },
    coin: { url: '/assets/audio/sfx/ui/coin.ogg', volume: 0.42 },
    levelup: { url: '/assets/audio/sfx/ui/levelup.ogg', volume: 0.55 },
    'quest-complete': {
      url: '/assets/audio/sfx/ui/quest_complete.ogg',
      volume: 0.55,
    },
    notification: { url: '/assets/audio/sfx/ui/notification.ogg', volume: 0.4 },
    pause: { url: '/assets/audio/sfx/ui/pause.ogg', volume: 0.45 },
    'game-over': { url: '/assets/audio/sfx/ui/game_over.ogg', volume: 0.55 },
    swing: { url: '/assets/audio/sfx/combat/swing.ogg', volume: 0.3 },
    'bow-shot': { url: '/assets/audio/sfx/combat/bow_shot.ogg', volume: 0.4 },
    'arrow-hit': {
      url: '/assets/audio/sfx/combat/arrow_hit.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'shield-block': {
      url: '/assets/audio/sfx/combat/shield_block.ogg',
      volume: 0.5,
    },

    // ── World SFX (spatial — use playSoundAt) ────────────────────────
    'bomb-drop': {
      url: '/assets/audio/sfx/world/bomb_drop.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'door-open': {
      url: '/assets/audio/sfx/world/door_open.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'door-close': {
      url: '/assets/audio/sfx/world/door_close.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'chest-open': {
      url: '/assets/audio/sfx/world/chest_open.ogg',
      volume: 0.55,
      ...worldSfx,
    },
    'fire-crackle': {
      url: '/assets/audio/sfx/world/fire_crackle.ogg',
      volume: 0.35,
      ...worldSfx,
      loop: true,
    },
    // Ambiência de água (gerada no pipeline Text2Sound — regen_sounds.py,
    // specs sfx/world/water_*): loops espaciais de rio/lago, ver
    // scripts/ambient-water.ts.
    'water-flow': {
      url: '/assets/audio/sfx/world/water_flow.ogg',
      volume: 0.3,
      ...worldSfx,
      loop: true,
    },
    'water-lake': {
      url: '/assets/audio/sfx/world/water_lake.ogg',
      volume: 0.24,
      ...worldSfx,
      loop: true,
    },
    'footsteps-grass': {
      url: '/assets/audio/sfx/world/footsteps_grass.ogg',
      volume: 0.3,
      ...worldSfx,
    },
    'enemy-hurt': {
      url: '/assets/audio/sfx/creatures/enemy_hurt.ogg',
      volume: 0.42,
      ...worldSfx,
    },
    'enemy-death': {
      url: '/assets/audio/sfx/creatures/enemy_death.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'boss-roar': {
      url: '/assets/audio/sfx/creatures/boss_roar.ogg',
      volume: 0.55,
      ...worldSfx,
      maxDistance: 48,
    },
    'slime-squish': {
      url: '/assets/audio/sfx/creatures/slime_squish.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'wolf-growl': {
      url: '/assets/audio/sfx/creatures/wolf_growl.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'item-drop': {
      url: '/assets/audio/sfx/world/item_drop.ogg',
      volume: 0.4,
      ...worldSfx,
    },
    'mine-hit': {
      url: '/assets/audio/sfx/combat/mine_hit.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'chop-hit': {
      url: '/assets/audio/sfx/combat/chop_hit.ogg',
      volume: 0.45,
      ...worldSfx,
    },
    'mine-break': {
      url: '/assets/audio/sfx/combat/mine_break.ogg',
      volume: 0.5,
      ...worldSfx,
    },
    'chop-break': {
      url: '/assets/audio/sfx/combat/chop_break.ogg',
      volume: 0.5,
      ...worldSfx,
    },

    // ── Music (bus 'music', looped, 2D) ──────────────────────────────
    'bgm-battle': {
      url: '/assets/audio/bgm/battle.ogg',
      volume: 0.22,
      bus: 'music',
      loop: true,
    },
    'bgm-explore': {
      url: '/assets/audio/bgm/explore.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
    'bgm-boss': {
      url: '/assets/audio/bgm/boss.ogg',
      volume: 0.22,
      bus: 'music',
      loop: true,
    },
    'bgm-village': {
      url: '/assets/audio/bgm/village.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
    'bgm-dungeon': {
      url: '/assets/audio/bgm/dungeon.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
    'bgm-mountain': {
      url: '/assets/audio/bgm/mountain.ogg',
      volume: 0.18,
      bus: 'music',
      loop: true,
    },
    'bgm-credits': {
      url: '/assets/audio/bgm/credits.ogg',
      volume: 0.2,
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
