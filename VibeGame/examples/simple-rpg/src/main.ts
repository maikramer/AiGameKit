declare global {
  interface Window {
    __heroPos?: () => { x: number; y: number; z: number; eid: number };
    __heroDebug?: () => Record<string, number>;
    __spawnFloatingText?: (
      text: string,
      x: number,
      y: number,
      z: number
    ) => number;
  }
}

import type { System, State, SoundHandle } from 'vibegame';
import {
  NavMeshPlugin,
  PlayerController,
  configure,
  disposeAllRuntimes,
  getBuilder,
  playSound,
  setBusVolume,
  setBusMuted,
  registerEntityScripts,
  resetBuilder,
  resumeAudioContextIfSuspended,
  setKTX2TranscoderPath,
  withPlugin,
  withSystem,
  LoadingPlugin,
  mountLoadingScreen,
  isWorldLoadedLatched,
  SaveLoadPlugin,
  I18nPlugin,
  saveToLocalStorage,
  loadFromLocalStorage,
  loadDictionary,
  setLocale,
  getLocale,
  t,
  isKeyDown,
  addInputMapping,
  spawnFloatingText,
  onDestructibleDestroyed,
  getScene,
} from 'vibegame';
import * as THREE from 'three';
import { defineQuery } from 'vibegame';
import {
  Rigidbody,
  Postprocessing,
  getRenderingContext,
  threeCameras,
  getBodyForEntity,
  getRapierWorld,
  PhysicsStepSystem,
  getBodyYForFeetAt,
  getCharacterFeetY,
  GROUND_CONTACT_SKIN,
  getTerrainHeightAt,
  getBvhSurfaceHeight,
  getTerrainContext,
  isTerrainDynamicsBlocking,
  Transform,
} from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';

setKTX2TranscoderPath('/libs/basis/');
import { CombatPlugin, DebugPlugin, Health, isDead } from 'vibegame';
import {
  addStone,
  getStoneCount,
  getLastCollectPosition,
} from './scripts/inventory';
import { addWood, getWoodCount } from './scripts/wood';
import { getGold } from './game/economy';
import { registerGameSounds } from './game/sounds';
import { isWoodEntity } from './scripts/tree';
import { anyCreatureAggro } from './scripts/creature';
import { anyBossAggro } from './scripts/boss';
import { NavMeshAgent, Destructible } from 'vibegame';
import { isShopOpen } from './game/pause';
import {
  createPauseMenu,
  type PauseMenu,
  type PauseOption,
} from './ui/pause-menu';
import {
  addSkillPoints,
  SKILL_POINTS_PER_LEVEL,
  setSkillEffectHandler,
} from './game/skills';
import { registerResource, defineItem, addItem } from './game/inventory';

const SAVE_KEY = 'simple-rpg-save';

function isTerrainReady(state: State): boolean {
  for (const [, data] of getTerrainContext(state)) {
    if (!data.initialized) continue;
    // A heightmapped terrain reports `initialized` with a flat zero-height
    // sampler before the image is decoded. Snapping then drops the hero far
    // below the real surface, where the one-sided heightfield can't catch it.
    // Wait until the heightmap data has actually loaded.
    if (data.heightmapUrl && !data.sampler.data) continue;
    return true;
  }
  return false;
}

let heroGroundSnapped = false;
let heroSpawnY: number | null = null;

const HeroGroundSnapSystem: System = {
  group: 'fixed',
  after: [PhysicsStepSystem],
  update(state: State) {
    if (heroGroundSnapped) return;

    const heroEid = state.getEntityByName('hero');
    if (heroEid === null || !state.hasComponent(heroEid, Transform)) return;

    const body = getBodyForEntity(state, heroEid);
    if (!body) return;

    const x = Transform.posX[heroEid];
    const z = Transform.posZ[heroEid];

    // Terrain not ready yet — freeze the hero at spawn Y so gravity
    // doesn't pull it through the floor before the heightmap loads.
    if (isTerrainDynamicsBlocking(state) || !isTerrainReady(state)) {
      if (heroSpawnY === null) heroSpawnY = Transform.posY[heroEid];
      Transform.posY[heroEid] = heroSpawnY;
      Transform.dirty[heroEid] = 1;
      Rigidbody.velX[heroEid] = 0;
      Rigidbody.velY[heroEid] = 0;
      Rigidbody.velZ[heroEid] = 0;
      body.setTranslation({ x, y: heroSpawnY, z }, true);
      body.setLinvel(new RAPIER.Vector3(0, 0, 0), true);
      const CM = state.getComponent('character-movement');
      if (CM && state.hasComponent(heroEid, CM)) CM.velocityY[heroEid] = 0;
      return;
    }

    const groundY =
      getBvhSurfaceHeight(state, x, 500, z) ?? getTerrainHeightAt(state, x, z);
    const snapY = getBodyYForFeetAt(
      state,
      heroEid,
      groundY + GROUND_CONTACT_SKIN
    );

    Transform.posX[heroEid] = x;
    Transform.posY[heroEid] = snapY;
    Transform.posZ[heroEid] = z;
    Transform.dirty[heroEid] = 1;

    Rigidbody.posX[heroEid] = x;
    Rigidbody.posY[heroEid] = snapY;
    Rigidbody.posZ[heroEid] = z;
    Rigidbody.velX[heroEid] = 0;
    Rigidbody.velY[heroEid] = 0;
    Rigidbody.velZ[heroEid] = 0;

    body.setTranslation({ x, y: snapY, z }, true);
    body.setLinvel(new RAPIER.Vector3(0, 0, 0), true);
    body.wakeUp();

    const CM = state.getComponent('character-movement');
    if (CM && state.hasComponent(heroEid, CM)) {
      CM.velocityY[heroEid] = 0;
      CM.desiredVelX[heroEid] = 0;
      CM.desiredVelZ[heroEid] = 0;
    }

    // Only release the hero once the *physics* world actually has ground
    // under the capsule. The visual heightmap (BVH) loads before the chunk
    // heightfield colliders are built; releasing early lets gravity build up
    // until the capsule tunnels straight through the one-sided heightfield.
    if (physicsGroundBelow(state, body, x, snapY, z)) {
      heroGroundSnapped = true;
    }
  },
};

const _downDir = { x: 0, y: -1, z: 0 };

/** True when a non-self collider lies within ~1.5m below the capsule. */
function physicsGroundBelow(
  state: State,
  body: RAPIER.RigidBody,
  x: number,
  y: number,
  z: number
): boolean {
  const world = getRapierWorld(state);
  if (!world || body.numColliders() === 0) return false;
  const collider = body.collider(0);
  const cp = collider.translation();
  const bp = body.translation();
  // Collider sits at a fixed offset from the body origin; cast from where the
  // collider would be if the body stood at (x, y, z).
  const origin = {
    x: x + (cp.x - bp.x),
    y: y + (cp.y - bp.y),
    z: z + (cp.z - bp.z),
  };
  const hit = world.castShape(
    origin,
    collider.rotation(),
    _downDir,
    collider.shape,
    0,
    1.5,
    true,
    RAPIER.QueryFilterFlags.EXCLUDE_SENSORS,
    undefined,
    undefined,
    undefined,
    (other: RAPIER.Collider) => other.handle !== collider.handle
  );
  return hit !== null;
}

// Live post-processing toggles (keys 1–4) so effects can be tuned per-machine —
// flipping a field and dropping context.postProcessing rebuilds the pipeline.
const postfxQuery = defineQuery([Postprocessing]);
const POSTFX_KEYS: Array<[string, string, string]> = [
  ['Digit1', 'bloom', 'Bloom'],
  ['Digit2', 'chromaticAberration', 'Chromatic Aberration'],
  ['Digit3', 'vignette', 'Vignette'],
  ['Digit4', 'aa', 'AA (SMAA/FXAA)'],
  ['Digit5', 'ssao', 'SSAO'],
  ['Digit6', 'toneMapping', 'Tone Mapping'],
];
const postfxDebounce = new Set<string>();

const PostFxToggleSystem: System = {
  group: 'simulation',
  update(state: State) {
    const ents = postfxQuery(state.world);
    if (ents.length === 0) return;
    const e = ents[0];
    for (const [code, field, label] of POSTFX_KEYS) {
      if (isKeyDown(code) && !postfxDebounce.has(code)) {
        postfxDebounce.add(code);
        const arr = (Postprocessing as Record<string, Uint8Array>)[field];
        if (field === 'aa') {
          arr[e] = ((arr[e] + 1) % 3) as 0 | 1 | 2;
        } else if (field === 'toneMapping') {
          arr[e] = ((arr[e] + 1) % 5) as 0 | 1 | 2 | 3 | 4;
        } else {
          arr[e] = arr[e] ? 0 : 1;
        }
        const ctx = getRenderingContext(state);
        ctx.postProcessing?.dispose();
        ctx.postProcessing = undefined;
        console.log(`[postfx] ${label} = ${arr[e] ? 'on' : 'off'}`);
      }
      if (!isKeyDown(code)) postfxDebounce.delete(code);
    }
  },
};

const dictEN: Record<string, string> = {
  'hud.title': 'Crystal Vale',
  'hud.mission': 'Explore, hunt creatures, gather & trade!',
  'hud.hp': 'HP: {hp} / {max}',
  'hud.stats': 'Gold: {gold} · Wood: {wood} · Stone: {stone}',
  'hud.enemies': 'Creatures nearby: {enemies}',
  'hud.time': 'Time: {time}',
  'hud.locale': 'Language: {lang}  [I] switch',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
  'hud.no-save': 'No save found.',
  'hud.healed': 'Health restored!',
  'hud.sold': 'Sold! +{gold} gold',
  'hud.gameOver': 'GAME OVER',
  'hud.waveReached': 'You survived',
  'hud.restart': 'Restart',
  'hud.controls':
    '[W/S] move  [A/D] turn  [Space] jump  [J] attack/chop  [K] talk/trade  [Q] menu',
  'hud.stone': 'Stone: {count}',
  'hud.stoneCollected': '+1 Stone!',
  'hint.merchant': 'Talk to Merchant',
  'hint.harvest.wood': 'Chop Tree',
  'hint.harvest.stone': 'Mine Rock',
  'tip.hp': 'Health — heal with potions',
  'tip.gold': 'Gold — spend at the merchant',
  'tip.wood': 'Wood — chop trees with [J]',
  'tip.stone': 'Stone — mine rocks with [J]',
  'tip.minimap':
    'Minimap — red: enemies · purple: boss · gold: merchant · green: tree · gray: rock',
  'tip.compass': 'Compass — facing direction',
  'minimap.you': 'You',
  'hud.levelUp': 'LEVEL UP!  Lv',
  'tip.xp': 'Experience — defeat creatures to level up',
  'tip.level': 'Level',
  'tip.enemies': 'Creatures nearby',
  'tip.time': 'Elapsed time',
  'pause.title': 'Paused',
  'pause.tab.menu': 'Menu',
  'pause.tab.skills': 'Skills',
  'pause.tab.inventory': 'Inventory',
  'pause.tab.options': 'Options',
  'pause.resume': 'Resume',
  'pause.save': 'Save',
  'pause.load': 'Load',
  'pause.restart': 'Restart',
  'pause.hint': '[Q] or [Esc] to resume',
  'pause.option.language': 'Language',
  'pause.option.music': 'Music',
  'pause.option.musicVol': 'Music Vol',
  'pause.option.sfx': 'Sound FX',
  'pause.option.sfxVol': 'SFX Vol',
  'opt.on': 'On',
  'opt.off': 'Off',
  'opt.mute': 'Mute',
  'opt.25': '25%',
  'opt.50': '50%',
  'opt.75': '75%',
  'opt.max': 'Max',
  'pause.points': 'Skill points: {n}',
  'pause.skill.vitality': 'Vitality',
  'pause.skill.vitality.desc': '+12 max HP',
  'pause.skill.strength': 'Strength',
  'pause.skill.strength.desc': 'Attack power (soon)',
  'pause.skill.agility': 'Agility',
  'pause.skill.agility.desc': 'Move speed (soon)',
  'pause.inventory.soon': 'Inventory coming soon',
};

const dictPT: Record<string, string> = {
  'hud.title': 'Vale do Cristal',
  'hud.mission': 'Explora, caça criaturas, recolhe e comercia!',
  'hud.hp': 'HP: {hp} / {max}',
  'hud.stats': 'Ouro: {gold} · Madeira: {wood} · Pedra: {stone}',
  'hud.enemies': 'Criaturas próximas: {enemies}',
  'hud.time': 'Tempo: {time}',
  'hud.locale': 'Idioma: {lang}  [I] trocar',
  'hud.saved': 'Jogo gravado!',
  'hud.loaded': 'Progresso restaurado.',
  'hud.no-save': 'Nenhuma gravação encontrada.',
  'hud.healed': 'Saúde restaurada!',
  'hud.sold': 'Vendido! +{gold} ouro',
  'hud.gameOver': 'FIM DE JOGO',
  'hud.waveReached': 'Sobreviveste',
  'hud.restart': 'Recomeçar',
  'hud.controls':
    '[W/S] mover  [A/D] virar  [Espaço] saltar  [J] atacar/cortar  [K] falar/comerciar  [Q] menu',
  'hud.stone': 'Pedra: {count}',
  'hud.stoneCollected': '+1 Pedra!',
  'hint.merchant': 'Falar com Mercador',
  'hint.harvest.wood': 'Cortar Árvore',
  'hint.harvest.stone': 'Minerar Rocha',
  'tip.hp': 'Vida — cura com poções',
  'tip.gold': 'Ouro — gasta no mercador',
  'tip.wood': 'Madeira — corta árvores com [J]',
  'tip.stone': 'Pedra — minera rochas com [J]',
  'tip.minimap':
    'Minimapa — vermelho: inimigos · roxo: chefe · ouro: mercador · verde: árvore · cinza: rocha',
  'tip.compass': 'Bússola — direção que encaras',
  'minimap.you': 'Tu',
  'hud.levelUp': 'SUBIU DE NÍVEL!  Nível',
  'tip.xp': 'Experiência — derrota criaturas para subir de nível',
  'tip.level': 'Nível',
  'tip.enemies': 'Criaturas próximas',
  'tip.time': 'Tempo decorrido',
  'pause.title': 'Pausa',
  'pause.tab.menu': 'Menu',
  'pause.tab.skills': 'Perícias',
  'pause.tab.inventory': 'Inventário',
  'pause.tab.options': 'Opções',
  'pause.resume': 'Continuar',
  'pause.save': 'Gravar',
  'pause.load': 'Carregar',
  'pause.restart': 'Recomeçar',
  'pause.hint': '[Q] ou [Esc] para continuar',
  'pause.option.language': 'Idioma',
  'pause.option.music': 'Música',
  'pause.option.musicVol': 'Vol Música',
  'pause.option.sfx': 'Efeitos',
  'pause.option.sfxVol': 'Vol Efeitos',
  'opt.on': 'Ligado',
  'opt.off': 'Desligado',
  'opt.mute': 'Mudo',
  'opt.25': '25%',
  'opt.50': '50%',
  'opt.75': '75%',
  'opt.max': 'Máx',
  'pause.points': 'Pontos de perícia: {n}',
  'pause.skill.vitality': 'Vitalidade',
  'pause.skill.vitality.desc': '+12 HP máx',
  'pause.skill.strength': 'Força',
  'pause.skill.strength.desc': 'Poder de ataque (em breve)',
  'pause.skill.agility': 'Agilidade',
  'pause.skill.agility.desc': 'Velocidade (em breve)',
  'pause.inventory.soon': 'Inventário em breve',
};

let overlayMissionEl: HTMLDivElement | null = null;
let enemiesChipEl: HTMLDivElement | null = null;
let timeChipEl: HTMLDivElement | null = null;
let overlayControlsEl: HTMLDivElement | null = null;
let healthBarFill: HTMLDivElement | null = null;
let healthBarText: HTMLSpanElement | null = null;
let damageFlashEl: HTMLDivElement | null = null;
let winEl: HTMLDivElement | null = null;
let hudRootEl: HTMLDivElement | null = null;
let stoneCountEl: HTMLDivElement | null = null;
let goldCountEl: HTMLDivElement | null = null;
let woodCountEl: HTMLDivElement | null = null;
let bossBarEl: HTMLDivElement | null = null;
let bossBarFill: HTMLDivElement | null = null;
let bossBarText: HTMLSpanElement | null = null;
let deathEl: HTMLDivElement | null = null;
let hudRevealed = false;

// Minimap + compass + interaction hint
let minimapCanvas: HTMLCanvasElement | null = null;
let minimapCtx: CanvasRenderingContext2D | null = null;
let compassStripEl: HTMLDivElement | null = null;
const compassMarks: { el: HTMLDivElement; az: number }[] = [];
let interactHintEl: HTMLDivElement | null = null;
let interactKeyEl: HTMLSpanElement | null = null;
let interactLabelEl: HTMLSpanElement | null = null;
const _camDir = new THREE.Vector3();
// Half-angle (radians) of world visible across the compass strip.
const COMPASS_FOV = 1.7;
const MINIMAP_RANGE = 60; // world meters from player edge-to-center
const MINIMAP_SIZE = 168; // px
const destructibleQuery = defineQuery([Destructible, Transform]);

let musicOn = true;
let sfxOn = true;

const VOLUME_LEVELS = [0, 0.25, 0.5, 0.75, 1.0] as const;
const VOLUME_LABELS = ['opt.mute', 'opt.25', 'opt.50', 'opt.75', 'opt.max'];
let musicVolIdx = 3;
let sfxVolIdx = 3;

// Music plays as three looped tracks on the 'music' bus; the battle track
// crossfades against the ambient tracks via per-handle volume (× bank base).
// The Music/SFX volume sliders drive the buses (see pause options below).
const BGM_BASE = { field: 0.18, explore: 0.18, battle: 0.22 } as const;
let bgmField: SoundHandle | null = null;
let bgmExplore: SoundHandle | null = null;
let bgmBattle: SoundHandle | null = null;
let battleMusicFade = 0;
let prevGoldFx = 0;

function applyBattleMusic(): void {
  const ambient = 1 - battleMusicFade;
  bgmField?.setVolume(BGM_BASE.field * ambient);
  bgmExplore?.setVolume(BGM_BASE.explore * ambient);
  bgmBattle?.setVolume(BGM_BASE.battle * battleMusicFade);
}

function updateBattleMusic(_state: State, dt: number): void {
  const target = anyCreatureAggro() || anyBossAggro() ? 1 : 0;
  const speed = Math.min(1, dt * 1.5);
  if (battleMusicFade < target)
    battleMusicFade = Math.min(target, battleMusicFade + speed);
  else if (battleMusicFade > target)
    battleMusicFade = Math.max(target, battleMusicFade - speed);
  applyBattleMusic();
}

/** Start/stop the background music tracks together (Options → Music). */
function setMusicPlaying(on: boolean): void {
  if (on) {
    if (bgmField) return; // already playing
    bgmField = playSound('bgm-field');
    bgmExplore = playSound('bgm-explore');
    bgmBattle = playSound('bgm-battle');
    applyBattleMusic();
  } else {
    bgmField?.stop();
    bgmExplore?.stop();
    bgmBattle?.stop();
    bgmField = bgmExplore = bgmBattle = null;
  }
}

function playSfx(key: string): void {
  if (sfxOn) playSound(key);
}

// ── Juice: screen shake ──────────────────────────────────────────────────
let gameCanvasEl: HTMLElement | null = null;
let shakeMag = 0;
let shakeUntil = 0;
/** Kick a screen shake. Strongest of overlapping requests wins. */
function addShake(mag: number, durMs: number): void {
  shakeMag = Math.max(shakeMag, mag);
  shakeUntil = Math.max(shakeUntil, performance.now() + durMs);
}
function updateShake(): void {
  if (!gameCanvasEl) gameCanvasEl = document.querySelector('#game-canvas');
  if (!gameCanvasEl) return;
  const now = performance.now();
  if (now >= shakeUntil || shakeMag <= 0.05) {
    if (shakeMag !== 0) gameCanvasEl.style.transform = '';
    shakeMag = 0;
    return;
  }
  const m = shakeMag;
  const dx = (Math.random() * 2 - 1) * m;
  const dy = (Math.random() * 2 - 1) * m;
  const rot = (Math.random() * 2 - 1) * m * 0.04;
  // Overscan (scale 1.04) hides the canvas edges revealed by the offset.
  gameCanvasEl.style.transform = `scale(1.04) translate(${dx}px,${dy}px) rotate(${rot}deg)`;
  shakeMag *= 0.86;
}

// ── XP / level ───────────────────────────────────────────────────────────
let xp = 0;
let level = 1;
let xpToNext = 6;
let levelUpFlashUntil = 0;
let xpBarFill: HTMLDivElement | null = null;
let levelBadgeEl: HTMLDivElement | null = null;
let levelUpEl: HTMLDivElement | null = null;

// Pause menu handle + Esc edge-debounce (UI built in ./ui/pause-menu).
let pauseMenu: PauseMenu | null = null;
let menuKeyDebounce = false;

function xpForLevel(l: number): number {
  return 5 + l * 4;
}
function addXp(state: State, amount: number): void {
  xp += amount;
  while (xp >= xpToNext) {
    xp -= xpToNext;
    level += 1;
    xpToNext = xpForLevel(level);
    addSkillPoints(SKILL_POINTS_PER_LEVEL);
    pauseMenu?.refresh();
    levelUpFlashUntil = state.time.elapsed + 2.0;
    if (levelUpEl) {
      levelUpEl.textContent = `${t(state, 'hud.levelUp')} ${level}`;
      levelUpEl.style.opacity = '1';
      levelUpEl.style.transform = 'translateX(-50%) translateY(0) scale(1)';
    }
    addShake(6, 260);
    playSfx('levelup');
  }
  if (xpBarFill) {
    xpBarFill.style.width = `${Math.max(0, Math.min(100, (xp / xpToNext) * 100))}%`;
  }
  if (levelBadgeEl) levelBadgeEl.textContent = `${level}`;
}

let prevHeroIsJumping = 0;
let prevPrimaryAction = 0;

let playTimeSec = 0;
let fpsEl: HTMLDivElement | null = null;
let fpsSmoothed = 60;
let fpsFrameCount = 0;
let fpsElapsed = 0;
let statusFlashUntil = 0;
let statusFlashKey = '';

let prevPlayerHp = 100;
let healFlashUntil = 0;
let prevWaveNumber = 1;
let waveCompleteFlashUntil = 0;
let winShown = false;
let prevStoneCollectVersion = 0;
let heroHealthInit = false;
let deathShown = false;
let respawnAtTime = 0;
const CHECKPOINT_X = 0;
const CHECKPOINT_Y = 50;
const CHECKPOINT_Z = 0;
const BOSS_BAR_RANGE = 50;
const RESPAWN_DELAY = 2.0;

// ── Unified floating text (damage, loot, hit sparks) ─────────────────────
// One screen-space pool projected from a 3D anchor each frame, so every kind of
// pop (player/enemy/boss damage, resource gains, harvest hits) shares the same
// look and motion instead of the old mix of DOM + troika 3D text.
interface FloatFx {
  x: number;
  y: number;
  z: number;
  born: number;
  dur: number;
  driftX: number;
  el: HTMLDivElement;
  active: boolean;
}
const FLOAT_POOL_SIZE = 32;
const floatPool: FloatFx[] = [];
let floatCursor = 0;

function formatTime(sec: number): string {
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60) % 60;
  const h = Math.floor(sec / 3600);
  if (h > 0)
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function pushFlash(state: State, key: string, seconds = 2.2): void {
  statusFlashKey = key;
  statusFlashUntil = state.time.elapsed + seconds;
}

const creatureQuery = defineQuery([NavMeshAgent, Health]);

function countAliveCreatures(state: State): number {
  let n = 0;
  for (const e of creatureQuery(state.world)) {
    if (state.hasComponent(e, PlayerController)) continue;
    if (Health.current[e] > 0) n++;
  }
  return n;
}

function refreshHud(state: State): void {
  if (enemiesChipEl) {
    enemiesChipEl.textContent = `⚔ ${countAliveCreatures(state)}`;
  }
  if (timeChipEl) {
    timeChipEl.textContent = `🕓 ${formatTime(playTimeSec)}`;
  }
}

function updateHealthBar(heroEid: number, state: State): void {
  if (!healthBarFill || !healthBarText) return;
  const maxHp = Health.max[heroEid] || 100;
  const hp = Math.max(0, Health.current[heroEid]);
  const pct = Math.max(0, Math.min(100, (hp / maxHp) * 100));
  healthBarFill.style.width = `${pct}%`;
  healthBarText.textContent = t(state, 'hud.hp', {
    hp: String(Math.round(hp)),
    max: String(Math.round(maxHp)),
  });
}

interface FloatOpts {
  color?: string;
  size?: number;
  dur?: number;
}

/** Spawn a floating pop anchored to a world position. Recycles the oldest pool
 * element; animation runs in updateFloatFx each frame. */
function pushFloat(
  state: State,
  wx: number,
  wy: number,
  wz: number,
  text: string,
  opts: FloatOpts = {}
): void {
  const fx = floatPool[floatCursor % FLOAT_POOL_SIZE];
  floatCursor++;
  if (!fx) return;
  fx.x = wx;
  fx.y = wy;
  fx.z = wz;
  fx.born = state.time.elapsed;
  fx.dur = opts.dur ?? 1.1;
  fx.driftX = (Math.random() - 0.5) * 34;
  fx.active = true;
  fx.el.textContent = text;
  fx.el.style.color = opts.color ?? '#ffffff';
  fx.el.style.fontSize = `${opts.size ?? 20}px`;
  fx.el.style.opacity = '0';
}

/** Project every live float to screen and animate rise + pop + fade. */
function updateFloatFx(state: State): void {
  const now = state.time.elapsed;
  for (const fx of floatPool) {
    if (!fx.active) continue;
    const t = (now - fx.born) / fx.dur;
    if (t >= 1) {
      fx.active = false;
      fx.el.style.opacity = '0';
      fx.el.style.transform = 'translate(-9999px,-9999px)';
      continue;
    }
    const p = project3Dto2D(fx.x, fx.y, fx.z, state);
    if (!p) {
      fx.el.style.opacity = '0';
      continue;
    }
    const rise = t * 54;
    const scale =
      t < 0.14
        ? 0.55 + (t / 0.14) * 0.55
        : 1.1 - Math.min(0.12, (t - 0.14) * 0.2);
    const alpha = t > 0.62 ? 1 - (t - 0.62) / 0.38 : 1;
    fx.el.style.transform = `translate(-50%,-50%) translate(${p.x + fx.driftX * t}px,${p.y - rise}px) scale(${scale})`;
    fx.el.style.opacity = String(alpha);
  }
}

const healthFxQuery = defineQuery([Health, Transform]);
const prevHpFx = new Map<number, number>();
const prevPendingFx = new Map<number, number>();

/** Watch every Health entity for a drop and pop a damage number + hurt SFX —
 * one place that covers player, enemies and the boss regardless of damage
 * source (melee, projectile, lunge). */
function watchCombatFx(state: State, heroEid: number | null): void {
  for (const e of healthFxQuery(state.world)) {
    const cur = Health.current[e];
    const prev = prevHpFx.get(e);
    prevHpFx.set(e, cur);
    if (prev === undefined) continue;
    if (cur >= prev - 0.01) continue;
    const dmg = Math.round(prev - cur);
    if (dmg <= 0) continue;
    const isHero = e === heroEid;
    // Bigger blows read as crits: larger, hotter, with an emphatic glyph.
    const big = !isHero && dmg >= 22;
    pushFloat(
      state,
      Transform.posX[e],
      Transform.posY[e] + (isHero ? 1.7 : 2.1),
      Transform.posZ[e],
      isHero ? `-${dmg}` : big ? `${dmg}!` : `${dmg}`,
      {
        color: isHero ? '#ff5a5a' : big ? '#ff8a2a' : '#fff0a0',
        size: isHero ? 23 : big ? 32 : 21,
        dur: big ? 1.3 : 1.1,
      }
    );
    if (isHero) {
      playSfx('player-hurt');
      addShake(Math.min(12, 4 + dmg * 0.25), 280);
    } else {
      playSfx('enemy-hurt');
      addShake(big ? 5 : 2.5, big ? 200 : 130);
      // Award XP once, on the hit that drops the creature to 0.
      if (cur <= 0 && prev > 0) {
        const maxHp = Health.max[e] || 30;
        addXp(state, Math.max(2, Math.round(maxHp / 12)));
      }
    }
  }
}

/** Per-swing harvest feedback: pop an impact spark + hit SFX at the moment the
 * blow actually lands. DestructibleSystem commits the hit on the button press
 * but delays the impact (Destructible.pendingImpact countdown ≈ end of swing);
 * we fire on the pending→0 transition so the spark matches the swing, not the
 * key press. The final hit destroys the prop (caught by the destroyed callback
 * which pops the resource gain), so this only covers non-breaking hits. */
function watchDestructibleFx(state: State): void {
  for (const e of destructibleQuery(state.world)) {
    const pend = Destructible.pendingImpact[e];
    const prev = prevPendingFx.get(e) ?? 0;
    prevPendingFx.set(e, pend);
    if (prev > 0 && pend <= 0) {
      const wood = isWoodEntity(e);
      pushFloat(
        state,
        Transform.posX[e],
        Transform.posY[e] + 1.6,
        Transform.posZ[e],
        '✦',
        { color: wood ? '#9be37a' : '#e2dccb', size: 24, dur: 0.55 }
      );
      if (wood) {
        playSfx('chop-hit');
      } else {
        playSfx('mine-hit');
      }
    }
  }
}

function project3Dto2D(
  worldX: number,
  worldY: number,
  worldZ: number,
  _state: State
): { x: number; y: number } | null {
  const camera = threeCameras.values().next().value;
  if (!camera) return null;

  const vec = new THREE.Vector3(worldX, worldY, worldZ);
  vec.project(camera);

  if (vec.z > 1) return null;

  const hw = window.innerWidth / 2;
  const hh = window.innerHeight / 2;

  return {
    x: vec.x * hw + hw,
    y: -vec.y * hh + hh,
  };
}

const GameplayHudSystem: System = {
  group: 'simulation',
  update(state: State) {
    // Keep the HUD hidden until the loading screen has handed off, then fade it
    // in — so it never shows over a still-loading world.
    if (!hudRevealed && isWorldLoadedLatched(state)) {
      hudRevealed = true;
      if (hudRootEl) hudRootEl.style.opacity = '1';
    }

    const dt = state.time.deltaTime;
    playTimeSec += dt;

    // FPS counter (smoothed)
    fpsFrameCount++;
    fpsElapsed += dt;
    if (fpsElapsed >= 0.5) {
      const instant = fpsFrameCount / fpsElapsed;
      fpsSmoothed = fpsSmoothed * 0.8 + instant * 0.2;
      fpsFrameCount = 0;
      fpsElapsed = 0;
      if (fpsEl) {
        if (fpsEl) {
          fpsEl.textContent = `FPS: ${Math.round(fpsSmoothed)} | ${(dt * 1000).toFixed(1)}ms`;
        }
      }
    }

    const heroEid = state.getEntityByName('hero');

    if (heroEid !== null && !heroHealthInit) {
      state.addComponent(heroEid, Health);
      Health.max[heroEid] = 100;
      Health.current[heroEid] = 100;
      heroHealthInit = true;
    }

    if (heroEid !== null && state.hasComponent(heroEid, Health)) {
      const currentHp = Health.current[heroEid];
      const maxHp = Health.max[heroEid];

      updateHealthBar(heroEid, state);

      // Red screen flash on hero damage. The damage number + hurt SFX are
      // emitted centrally by watchCombatFx (covers every Health entity), so
      // this block only owns the full-screen flash now.
      if (currentHp < prevPlayerHp && prevPlayerHp > 0) {
        if (damageFlashEl) {
          damageFlashEl.style.transition = 'none';
          damageFlashEl.style.opacity = '1';
          void damageFlashEl.offsetHeight;
          damageFlashEl.style.transition = 'opacity 0.2s ease-out';
          damageFlashEl.style.opacity = '0';
        }
      }

      if (state.time.elapsed < healFlashUntil && healFlashUntil > 0) {
        if (overlayMissionEl) {
          overlayMissionEl.innerHTML =
            `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
            `<span style="opacity:0.95;font-size:13px;color:#88ffaa">${t(state, 'hud.healed')}</span>`;
        }
      }

      prevPlayerHp = currentHp;

      if (isDead(heroEid) && !deathShown) {
        deathShown = true;
        respawnAtTime = state.time.elapsed + RESPAWN_DELAY;
        if (deathEl) deathEl.style.display = 'flex';
      }
      if (deathShown && state.time.elapsed >= respawnAtTime) {
        Health.current[heroEid] = Health.max[heroEid];
        const body = getBodyForEntity(state, heroEid);
        Transform.posX[heroEid] = CHECKPOINT_X;
        Transform.posY[heroEid] = CHECKPOINT_Y;
        Transform.posZ[heroEid] = CHECKPOINT_Z;
        Transform.dirty[heroEid] = 1;
        Rigidbody.velX[heroEid] = 0;
        Rigidbody.velY[heroEid] = 0;
        Rigidbody.velZ[heroEid] = 0;
        if (body) {
          body.setTranslation(
            { x: CHECKPOINT_X, y: CHECKPOINT_Y, z: CHECKPOINT_Z },
            true
          );
          body.setLinvel(new RAPIER.Vector3(0, 0, 0), true);
          body.wakeUp();
        }
        heroGroundSnapped = false;
        prevPlayerHp = Health.max[heroEid];
        deathShown = false;
        if (deathEl) deathEl.style.display = 'none';
      }
    }

    const bossEid = state.getEntityByName('boss');
    if (
      bossBarEl &&
      bossEid !== null &&
      heroEid !== null &&
      state.hasComponent(bossEid, Health) &&
      state.hasComponent(heroEid, Transform)
    ) {
      const dx = Transform.posX[bossEid] - Transform.posX[heroEid];
      const dz = Transform.posZ[bossEid] - Transform.posZ[heroEid];
      const near = dx * dx + dz * dz < BOSS_BAR_RANGE * BOSS_BAR_RANGE;
      if (near && !isDead(bossEid)) {
        bossBarEl.style.display = 'block';
        const ratio =
          Health.max[bossEid] > 0
            ? Math.max(
                0,
                Math.min(1, Health.current[bossEid] / Health.max[bossEid])
              )
            : 0;
        if (bossBarFill)
          bossBarFill.style.width = `${(ratio * 100).toFixed(1)}%`;
        if (bossBarText)
          bossBarText.textContent = `Boss Ogre: ${Math.round(Health.current[bossEid])} / ${Math.round(Health.max[bossEid])}`;
      } else {
        bossBarEl.style.display = 'none';
      }
    } else if (bossBarEl) {
      bossBarEl.style.display = 'none';
    }

    if (
      bossEid !== null &&
      state.hasComponent(bossEid, Health) &&
      isDead(bossEid) &&
      !winShown
    ) {
      winShown = true;
      if (winEl) winEl.style.display = 'flex';
    }

    if (statusFlashKey && state.time.elapsed >= statusFlashUntil) {
      statusFlashKey = '';
    }

    const currentStone = getStoneCount();
    if (stoneCountEl) {
      stoneCountEl.textContent = `🪨 ${currentStone}`;
    }
    const currentGold = getGold();
    if (goldCountEl) {
      goldCountEl.textContent = `🪙 ${currentGold}`;
    }
    if (woodCountEl) {
      woodCountEl.textContent = `🪵 ${getWoodCount()}`;
    }
    // Gold-gain pop near the hero (loot from kills, sales).
    if (currentGold > prevGoldFx && heroEid !== null) {
      pushFloat(
        state,
        Transform.posX[heroEid],
        Transform.posY[heroEid] + 2.3,
        Transform.posZ[heroEid],
        `+${currentGold - prevGoldFx} 🪙`,
        { color: '#ffd24a', size: 20 }
      );
    }
    prevGoldFx = currentGold;
    const collectPos = getLastCollectPosition();
    if (collectPos.version !== prevStoneCollectVersion) {
      prevStoneCollectVersion = collectPos.version;
      playSfx('coin');
    }
    if (
      overlayMissionEl &&
      statusFlashKey &&
      state.time.elapsed < statusFlashUntil
    ) {
      const extra = t(state, statusFlashKey);
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.95;font-size:13px;color:#c8e0ff">${extra}</span>`;
    } else if (overlayMissionEl && state.time.elapsed >= healFlashUntil) {
      overlayMissionEl.innerHTML =
        `<strong style="font-size:15px;letter-spacing:0.4px">${t(state, 'hud.title')}</strong><br/>` +
        `<span style="opacity:0.88;font-size:13px">${t(state, 'hud.mission')}</span>`;
    }
    refreshHud(state);

    watchCombatFx(state, heroEid);
    watchDestructibleFx(state);
    updateBattleMusic(state, dt);
    updateFloatFx(state);
    updateShake();
    if (
      levelUpEl &&
      levelUpFlashUntil > 0 &&
      state.time.elapsed >= levelUpFlashUntil
    ) {
      levelUpEl.style.opacity = '0';
      levelUpEl.style.transform =
        'translateX(-50%) translateY(-12px) scale(0.9)';
      levelUpFlashUntil = 0;
    }

    if (
      hudRevealed &&
      heroEid !== null &&
      state.hasComponent(heroEid, Transform)
    ) {
      drawMinimap(state, heroEid);
      updateCompass();
      updateInteractionHint(state, heroEid);
    }

    if (heroEid !== null && state.hasComponent(heroEid, PlayerController)) {
      const jumping = PlayerController.isJumping[heroEid];
      if (jumping === 1 && prevHeroIsJumping === 0) {
        playSfx('jump');
      }
      prevHeroIsJumping = jumping;
    }

    if (heroEid !== null && state.hasComponent(heroEid, InputState)) {
      const primary = InputState.primaryAction[heroEid];
      if (primary === 1 && prevPrimaryAction === 0) {
        playSfx('swing');
      }
      prevPrimaryAction = primary;
    }

    // Q (or Esc) toggles the pause menu — which also hosts Save/Load/Options.
    // Skipped while the merchant shop is open (Esc there closes the shop).
    const menuKey = isKeyDown('KeyQ') || isKeyDown('Escape');
    if (menuKey && !menuKeyDebounce) {
      menuKeyDebounce = true;
      if (!isShopOpen()) pauseMenu?.toggle();
    }
    if (!menuKey) menuKeyDebounce = false;
  },
};

/** Camera forward azimuth: 0=+Z(south), +π/2=+X(east), ±π=-Z(north). */
function getCameraAzimuth(): number {
  const camera = threeCameras.values().next().value as THREE.Camera | undefined;
  if (!camera) return 0;
  camera.getWorldDirection(_camDir);
  return Math.atan2(_camDir.x, _camDir.z);
}

function wrapAngle(a: number): number {
  while (a > Math.PI) a -= Math.PI * 2;
  while (a < -Math.PI) a += Math.PI * 2;
  return a;
}

/** Scrolling cardinal compass: each mark slides based on its world azimuth
 * relative to the camera heading; the centre of the strip is where you face. */
function updateCompass(): void {
  if (!compassStripEl || compassMarks.length === 0) return;
  const camAz = getCameraAzimuth();
  const halfW = compassStripEl.clientWidth / 2;
  for (const m of compassMarks) {
    const off = wrapAngle(m.az - camAz);
    if (Math.abs(off) > COMPASS_FOV) {
      m.el.style.opacity = '0';
      continue;
    }
    const px = (off / COMPASS_FOV) * halfW;
    const fade = 1 - Math.abs(off) / COMPASS_FOV;
    m.el.style.transform = `translateX(${px}px)`;
    m.el.style.opacity = String(0.25 + fade * 0.75);
  }
}

interface MapDot {
  x: number;
  z: number;
  color: string;
  r: number;
}

const _mapDots: MapDot[] = [];

/** Top-down minimap, north-up, player fixed at centre. Plots live enemies,
 * boss, merchant and harvestable resource nodes within MINIMAP_RANGE. */
function drawMinimap(state: State, heroEid: number): void {
  if (!minimapCtx || !minimapCanvas) return;
  const ctx = minimapCtx;
  const S = MINIMAP_SIZE;
  const cx = S / 2;
  const cz = S / 2;
  const scale = S / 2 / MINIMAP_RANGE;

  const px = Transform.posX[heroEid];
  const pz = Transform.posZ[heroEid];

  ctx.clearRect(0, 0, S, S);

  // Circular ground disc.
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cz, S / 2 - 2, 0, Math.PI * 2);
  ctx.clip();
  ctx.fillStyle = 'rgba(18,26,40,0.82)';
  ctx.fillRect(0, 0, S, S);
  // Range rings.
  ctx.strokeStyle = 'rgba(120,150,210,0.12)';
  ctx.lineWidth = 1;
  for (let i = 1; i <= 2; i++) {
    ctx.beginPath();
    ctx.arc(cx, cz, (S / 2 - 2) * (i / 2.4), 0, Math.PI * 2);
    ctx.stroke();
  }

  // Collect dots.
  _mapDots.length = 0;
  const bossEid = state.getEntityByName('boss');
  const merchantEid = state.getEntityByName('merchant');
  for (const e of creatureQuery(state.world)) {
    if (state.hasComponent(e, PlayerController)) continue;
    if (Health.current[e] <= 0) continue;
    if (e === bossEid) continue;
    _mapDots.push({
      x: Transform.posX[e],
      z: Transform.posZ[e],
      color: '#ff4d4d',
      r: 2.6,
    });
  }
  if (
    bossEid !== null &&
    state.hasComponent(bossEid, Health) &&
    !isDead(bossEid)
  ) {
    _mapDots.push({
      x: Transform.posX[bossEid],
      z: Transform.posZ[bossEid],
      color: '#c060ff',
      r: 4.5,
    });
  }
  if (merchantEid !== null && state.hasComponent(merchantEid, Transform)) {
    _mapDots.push({
      x: Transform.posX[merchantEid],
      z: Transform.posZ[merchantEid],
      color: '#ffd24a',
      r: 3.5,
    });
  }
  for (const e of destructibleQuery(state.world)) {
    _mapDots.push({
      x: Transform.posX[e],
      z: Transform.posZ[e],
      color: isWoodEntity(e) ? '#6fdc6f' : '#b9b2a6',
      r: 1.8,
    });
  }

  // Draw, clamped to disc edge with a faded off-range style.
  const maxPix = S / 2 - 6;
  for (const d of _mapDots) {
    const rx = (d.x - px) * scale;
    const rz = -(d.z - pz) * scale; // north (−Z world) = up
    const dist = Math.hypot(rx, rz);
    let dx = rx;
    let dz = rz;
    let edge = false;
    if (dist > maxPix) {
      dx = (rx / dist) * maxPix;
      dz = (rz / dist) * maxPix;
      edge = true;
    }
    ctx.beginPath();
    ctx.arc(cx + dx, cz + dz, edge ? d.r * 0.7 : d.r, 0, Math.PI * 2);
    ctx.fillStyle = d.color;
    ctx.globalAlpha = edge ? 0.5 : 1;
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  // Player arrow at centre, rotated to facing (eulerY = atan2(dirX,dirZ)).
  const heading = Transform.eulerY[heroEid] || 0;
  ctx.save();
  ctx.translate(cx, cz);
  ctx.rotate(-heading); // screen up = north; arrow points facing
  ctx.beginPath();
  ctx.moveTo(0, -7);
  ctx.lineTo(5, 6);
  ctx.lineTo(0, 3);
  ctx.lineTo(-5, 6);
  ctx.closePath();
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = 'rgba(0,0,0,0.55)';
  ctx.lineWidth = 1;
  ctx.fill();
  ctx.stroke();
  ctx.restore();

  ctx.restore();

  // North tick on the rim.
  ctx.fillStyle = 'rgba(255,255,255,0.85)';
  ctx.font = '700 9px system-ui,sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText('N', cx, 11);
}

/** Show a contextual action prompt for the nearest interactable in range
 * (merchant to talk, tree/rock to harvest). Purely a hint — the entity scripts
 * still own the actual key handling. */
function updateInteractionHint(state: State, heroEid: number): void {
  if (!interactHintEl || !interactKeyEl || !interactLabelEl) return;
  const px = Transform.posX[heroEid];
  const pz = Transform.posZ[heroEid];

  let bestDist = Infinity;
  let bestKey = '';
  let bestLabel = '';

  const merchantEid = state.getEntityByName('merchant');
  if (merchantEid !== null && state.hasComponent(merchantEid, Transform)) {
    const dx = Transform.posX[merchantEid] - px;
    const dz = Transform.posZ[merchantEid] - pz;
    const d = dx * dx + dz * dz;
    if (d < 4.5 * 4.5 && d < bestDist) {
      bestDist = d;
      bestKey = 'K';
      bestLabel = t(state, 'hint.merchant');
    }
  }

  for (const e of destructibleQuery(state.world)) {
    const range = (Destructible.range[e] || 3.5) + 0.5;
    const dx = Transform.posX[e] - px;
    const dz = Transform.posZ[e] - pz;
    const d = dx * dx + dz * dz;
    if (d < range * range && d < bestDist) {
      bestDist = d;
      bestKey = 'J';
      bestLabel = t(
        state,
        isWoodEntity(e) ? 'hint.harvest.wood' : 'hint.harvest.stone'
      );
    }
  }

  if (bestKey) {
    interactKeyEl.textContent = bestKey;
    interactLabelEl.textContent = bestLabel;
    interactHintEl.style.opacity = '1';
    interactHintEl.style.transform = 'translateX(-50%) translateY(0)';
  } else {
    interactHintEl.style.opacity = '0';
    interactHintEl.style.transform = 'translateX(-50%) translateY(8px)';
  }
}

function createOverlayHud(state: State): void {
  const wrap = document.createElement('div');
  wrap.style.cssText =
    'position:fixed;inset:0;pointer-events:none;z-index:1000;font-family:system-ui,Segoe UI,sans-serif;' +
    // Hidden until the loading screen finishes, so the HUD doesn't show over a
    // half-built scene. Revealed by GameplayHudSystem once the world is loaded.
    'opacity:0;transition:opacity 0.5s ease-in;';
  hudRootEl = wrap;

  const topLeft = document.createElement('div');
  topLeft.style.cssText =
    'position:absolute;top:20px;left:20px;max-width:min(420px,92vw);' +
    'display:flex;flex-direction:column;gap:10px;';

  overlayMissionEl = document.createElement('div');
  overlayMissionEl.style.cssText =
    'background:rgba(8,12,28,0.72);color:#e8eef8;padding:12px 18px;' +
    'border-radius:10px;font-size:13px;line-height:1.45;' +
    'border:1px solid rgba(90,120,200,0.22);backdrop-filter:blur(10px);' +
    'box-shadow:0 8px 32px rgba(0,0,0,0.25);';

  const healthBarContainer = document.createElement('div');
  healthBarContainer.title = t(state, 'tip.hp');
  healthBarContainer.style.cssText =
    'background:linear-gradient(135deg,rgba(14,18,34,0.72),rgba(10,14,26,0.6));' +
    'border-radius:12px;padding:10px 14px;display:flex;align-items:center;gap:10px;' +
    'border:1px solid rgba(120,150,220,0.2);backdrop-filter:blur(10px);' +
    'box-shadow:0 6px 22px rgba(0,0,0,0.28);pointer-events:auto;';

  const heartIcon = document.createElement('span');
  heartIcon.textContent = '❤';
  heartIcon.style.cssText =
    'font-size:18px;color:#ff5a6e;filter:drop-shadow(0 1px 2px rgba(0,0,0,0.5));';

  const healthBarOuter = document.createElement('div');
  healthBarOuter.style.cssText =
    'flex:1;height:22px;background:rgba(60,20,20,0.6);border-radius:6px;' +
    'position:relative;overflow:hidden;min-width:190px;' +
    'box-shadow:inset 0 1px 3px rgba(0,0,0,0.5);';

  healthBarFill = document.createElement('div');
  healthBarFill.style.cssText =
    'width:100%;height:100%;background:linear-gradient(90deg,#1f9d35,#3ee06a 60%,#8cf5a8);' +
    'border-radius:6px;transition:width 0.15s ease-out;' +
    'box-shadow:0 0 8px rgba(60,220,110,0.45);';

  healthBarText = document.createElement('span');
  healthBarText.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'color:#fff;font-size:11px;font-weight:600;text-shadow:0 1px 3px rgba(0,0,0,0.6);';
  healthBarText.textContent = t(state, 'hud.hp', { hp: '100', max: '100' });

  healthBarOuter.appendChild(healthBarFill);
  healthBarOuter.appendChild(healthBarText);
  healthBarContainer.appendChild(heartIcon);
  healthBarContainer.appendChild(healthBarOuter);

  topLeft.appendChild(overlayMissionEl);
  topLeft.appendChild(healthBarContainer);

  // XP bar + level badge.
  const xpRow = document.createElement('div');
  xpRow.style.cssText = 'display:flex;align-items:center;gap:8px;';

  levelBadgeEl = document.createElement('div');
  levelBadgeEl.title = t(state, 'tip.level');
  levelBadgeEl.style.cssText =
    'flex:0 0 auto;width:30px;height:30px;border-radius:50%;' +
    'display:flex;align-items:center;justify-content:center;' +
    'font:800 14px system-ui,sans-serif;color:#2a1c06;pointer-events:auto;' +
    'background:radial-gradient(circle at 35% 30%,#ffe7a0,#ffc24a 60%,#d99320);' +
    'border:1px solid rgba(255,225,150,0.85);' +
    'box-shadow:0 3px 10px rgba(0,0,0,0.4),inset 0 1px 2px rgba(255,255,255,0.65);';
  levelBadgeEl.textContent = '1';

  const xpOuter = document.createElement('div');
  xpOuter.title = t(state, 'tip.xp');
  xpOuter.style.cssText =
    'flex:1;height:9px;border-radius:6px;background:rgba(10,14,26,0.7);' +
    'overflow:hidden;pointer-events:auto;' +
    'border:1px solid rgba(120,150,220,0.2);box-shadow:inset 0 1px 2px rgba(0,0,0,0.5);';
  xpBarFill = document.createElement('div');
  xpBarFill.style.cssText =
    'width:0%;height:100%;border-radius:6px;' +
    'background:linear-gradient(90deg,#7a5cff,#b18cff 60%,#e6dbff);' +
    'box-shadow:0 0 8px rgba(150,110,255,0.6);transition:width 0.25s ease-out;';
  xpOuter.appendChild(xpBarFill);
  xpRow.appendChild(levelBadgeEl);
  xpRow.appendChild(xpOuter);
  topLeft.appendChild(xpRow);

  const chipRow = document.createElement('div');
  chipRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

  const chipCss = (accent: string): string =>
    'background:linear-gradient(135deg,rgba(14,18,34,0.72),rgba(10,14,26,0.6));' +
    'border-radius:10px;padding:7px 13px;' +
    `border:1px solid ${accent};backdrop-filter:blur(10px);` +
    'box-shadow:0 5px 18px rgba(0,0,0,0.25);' +
    'font-size:14px;font-weight:700;display:flex;align-items:center;gap:7px;' +
    'pointer-events:auto;cursor:default;';

  goldCountEl = document.createElement('div');
  goldCountEl.style.cssText =
    chipCss('rgba(255,210,60,0.3)') + 'color:#ffd24a;';
  goldCountEl.title = t(state, 'tip.gold');
  goldCountEl.textContent = '🪙 0';

  woodCountEl = document.createElement('div');
  woodCountEl.style.cssText =
    chipCss('rgba(190,140,80,0.3)') + 'color:#d4a76a;';
  woodCountEl.title = t(state, 'tip.wood');
  woodCountEl.textContent = '🪵 0';

  stoneCountEl = document.createElement('div');
  stoneCountEl.style.cssText =
    chipCss('rgba(170,165,150,0.3)') + 'color:#d4c9a8;';
  stoneCountEl.title = t(state, 'tip.stone');
  stoneCountEl.textContent = '🪨 0';

  chipRow.appendChild(goldCountEl);
  chipRow.appendChild(woodCountEl);
  chipRow.appendChild(stoneCountEl);
  topLeft.appendChild(chipRow);

  // Status chips: nearby creatures + elapsed time (replaces the old verbose
  // text panel; language hint lives in the bottom controls bar).
  const statRow = document.createElement('div');
  statRow.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';

  enemiesChipEl = document.createElement('div');
  enemiesChipEl.style.cssText =
    chipCss('rgba(200,80,80,0.28)') + 'color:#ff9a9a;';
  enemiesChipEl.title = t(state, 'tip.enemies');
  enemiesChipEl.textContent = '⚔ 0';

  timeChipEl = document.createElement('div');
  timeChipEl.style.cssText =
    chipCss('rgba(120,150,210,0.24)') + 'color:#b8c8e8;';
  timeChipEl.title = t(state, 'tip.time');
  timeChipEl.textContent = '🕓 0:00';

  statRow.appendChild(enemiesChipEl);
  statRow.appendChild(timeChipEl);
  topLeft.appendChild(statRow);

  // Hurt feedback: a red vignette that bleeds in from the screen edges (rather
  // than a flat full-screen wash), pulsed on hit by GameplayHudSystem.
  damageFlashEl = document.createElement('div');
  damageFlashEl.style.cssText =
    'position:fixed;inset:0;pointer-events:none;opacity:0;z-index:1001;' +
    'background:radial-gradient(ellipse at center,' +
    'rgba(255,0,0,0) 38%,rgba(180,0,0,0.35) 78%,rgba(120,0,0,0.7) 100%);';

  // Level-up banner (center).
  levelUpEl = document.createElement('div');
  levelUpEl.style.cssText =
    'position:fixed;top:24%;left:50%;transform:translateX(-50%) translateY(-12px) scale(0.9);' +
    'z-index:1004;pointer-events:none;white-space:nowrap;opacity:0;' +
    'font:800 30px system-ui,Segoe UI,sans-serif;letter-spacing:1.5px;' +
    'color:#ffe9a8;text-shadow:0 0 16px rgba(255,200,80,0.7),0 2px 8px rgba(0,0,0,0.6);' +
    'transition:opacity 0.3s ease,transform 0.3s cubic-bezier(0.2,1.4,0.4,1);';
  levelUpEl.textContent = '';
  wrap.appendChild(levelUpEl);

  for (let i = 0; i < FLOAT_POOL_SIZE; i++) {
    const el = document.createElement('div');
    el.style.cssText =
      'position:fixed;left:0;top:0;pointer-events:none;z-index:1002;' +
      'font-family:system-ui,Segoe UI,sans-serif;font-weight:800;white-space:nowrap;' +
      'text-shadow:0 0 4px rgba(0,0,0,0.9),0 2px 3px rgba(0,0,0,0.85);' +
      '-webkit-text-stroke:0.6px rgba(0,0,0,0.5);' +
      'will-change:transform,opacity;opacity:0;transform:translate(-9999px,-9999px);';
    wrap.appendChild(el);
    floatPool.push({
      x: 0,
      y: 0,
      z: 0,
      born: 0,
      dur: 1,
      driftX: 0,
      el,
      active: false,
    });
  }

  winEl = document.createElement('div');
  winEl.style.cssText =
    'position:fixed;inset:0;z-index:2000;' +
    'background:radial-gradient(ellipse at center,rgba(40,30,8,0.86),rgba(0,0,0,0.94));' +
    'display:none;flex-direction:column;align-items:center;justify-content:center;' +
    'pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;';

  const winCrown = document.createElement('div');
  winCrown.textContent = '👑';
  winCrown.style.cssText =
    'font-size:64px;margin-bottom:8px;filter:drop-shadow(0 4px 18px rgba(255,200,60,0.6));';

  const winTitle = document.createElement('div');
  winTitle.style.cssText =
    'font-size:64px;font-weight:800;letter-spacing:4px;margin-bottom:14px;' +
    'background:linear-gradient(180deg,#fff3c4,#ffd24a 55%,#e09a20);' +
    '-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;' +
    'filter:drop-shadow(0 2px 18px rgba(255,200,0,0.5));';
  winTitle.textContent = 'VICTORY!';

  const winSub = document.createElement('div');
  winSub.style.cssText =
    'color:#e8eef8;font-size:22px;margin-bottom:30px;text-align:center;opacity:0.9;';
  winSub.textContent = 'You defeated the Boss Ogre!';

  const winBtn = document.createElement('button');
  winBtn.style.cssText =
    'background:linear-gradient(180deg,rgba(150,110,40,0.5),rgba(90,65,20,0.5));' +
    'color:#ffe08a;border:1px solid rgba(255,210,120,0.5);' +
    'padding:13px 40px;border-radius:10px;font-size:16px;font-weight:700;cursor:pointer;' +
    'pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;letter-spacing:0.5px;' +
    'box-shadow:0 6px 22px rgba(0,0,0,0.45);transition:transform 0.12s ease,box-shadow 0.12s ease;';
  winBtn.textContent = '↻ Play Again';
  winBtn.addEventListener('click', () => location.reload());
  winBtn.addEventListener('mouseenter', () => {
    winBtn.style.transform = 'translateY(-2px)';
    winBtn.style.boxShadow = '0 10px 28px rgba(255,200,80,0.3)';
  });
  winBtn.addEventListener('mouseleave', () => {
    winBtn.style.transform = '';
    winBtn.style.boxShadow = '0 6px 22px rgba(0,0,0,0.45)';
  });

  winEl.appendChild(winCrown);
  winEl.appendChild(winTitle);
  winEl.appendChild(winSub);
  winEl.appendChild(winBtn);

  deathEl = document.createElement('div');
  deathEl.style.cssText =
    'position:fixed;top:35%;left:50%;transform:translate(-50%,-50%);z-index:1900;' +
    'background:linear-gradient(160deg,rgba(60,4,4,0.9),rgba(28,0,0,0.88));' +
    'color:#ff7070;padding:26px 52px;border-radius:14px;' +
    'font:800 28px system-ui,Segoe UI,sans-serif;letter-spacing:1.5px;' +
    'border:1px solid rgba(255,70,70,0.45);' +
    'box-shadow:0 12px 48px rgba(0,0,0,0.6),0 0 40px rgba(180,0,0,0.25);' +
    'text-shadow:0 2px 10px rgba(0,0,0,0.6);' +
    'pointer-events:none;display:none;text-align:center;';
  deathEl.textContent = '☠  You Died — Respawning...';

  bossBarEl = document.createElement('div');
  bossBarEl.style.cssText =
    'position:fixed;top:58px;left:50%;transform:translateX(-50%);z-index:1000;' +
    'background:rgba(8,12,28,0.7);border-radius:10px;padding:10px 16px;' +
    'border:1px solid rgba(200,40,40,0.35);backdrop-filter:blur(8px);' +
    'display:none;min-width:320px;';

  const bossBarOuter = document.createElement('div');
  bossBarOuter.style.cssText =
    'width:300px;height:20px;background:rgba(60,12,12,0.7);border-radius:4px;' +
    'position:relative;overflow:hidden;margin:0 auto;';

  bossBarFill = document.createElement('div');
  bossBarFill.style.cssText =
    'width:100%;height:100%;background:linear-gradient(90deg,#b22222,#ff4444);' +
    'border-radius:4px;transition:width 0.2s ease-out;';

  bossBarText = document.createElement('span');
  bossBarText.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'color:#fff;font-size:11px;font-weight:600;text-shadow:0 1px 3px rgba(0,0,0,0.7);';
  bossBarText.textContent = 'Boss Ogre';

  bossBarOuter.appendChild(bossBarFill);
  bossBarOuter.appendChild(bossBarText);
  bossBarEl.appendChild(bossBarOuter);

  const bottom = document.createElement('div');
  bottom.style.cssText =
    'position:absolute;bottom:22px;left:50%;transform:translateX(-50%);';

  overlayControlsEl = document.createElement('div');
  overlayControlsEl.style.cssText =
    'background:rgba(8,12,28,0.5);color:#8a9ab8;padding:8px 16px;' +
    'border-radius:8px;font-size:11px;letter-spacing:0.2px;max-width:92vw;text-align:center;' +
    'border:1px solid rgba(90,120,200,0.12);';
  overlayControlsEl.textContent = t(state, 'hud.controls');

  bottom.appendChild(overlayControlsEl);
  wrap.appendChild(topLeft);
  wrap.appendChild(damageFlashEl);
  wrap.appendChild(bossBarEl);
  wrap.appendChild(deathEl);
  wrap.appendChild(bottom);

  // ── Minimap (top-right) ──────────────────────────────────────────────
  const minimapWrap = document.createElement('div');
  minimapWrap.title = t(state, 'tip.minimap');
  minimapWrap.style.cssText =
    `position:fixed;top:18px;right:18px;width:${MINIMAP_SIZE}px;height:${MINIMAP_SIZE}px;` +
    'border-radius:50%;z-index:1000;pointer-events:auto;' +
    'border:2px solid rgba(150,180,240,0.35);' +
    'box-shadow:0 8px 30px rgba(0,0,0,0.4),inset 0 0 18px rgba(0,0,0,0.5);' +
    'background:rgba(8,12,28,0.4);';

  minimapCanvas = document.createElement('canvas');
  minimapCanvas.width = MINIMAP_SIZE;
  minimapCanvas.height = MINIMAP_SIZE;
  minimapCanvas.style.cssText = `width:${MINIMAP_SIZE}px;height:${MINIMAP_SIZE}px;border-radius:50%;display:block;`;
  minimapCtx = minimapCanvas.getContext('2d');
  minimapWrap.appendChild(minimapCanvas);
  wrap.appendChild(minimapWrap);

  // FPS counter (under the minimap)
  fpsEl = document.createElement('div');
  fpsEl.style.cssText =
    `position:fixed;top:${18 + MINIMAP_SIZE + 8}px;right:18px;` +
    'background:rgba(8,12,28,0.55);color:#aabbcc;padding:5px 11px;' +
    'border-radius:6px;font-size:11px;font-weight:600;font-family:monospace;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(6px);' +
    'z-index:1000;pointer-events:none;';
  fpsEl.textContent = 'FPS: --';
  wrap.appendChild(fpsEl);

  // ── Compass strip (top-center) ───────────────────────────────────────
  compassStripEl = document.createElement('div');
  compassStripEl.title = t(state, 'tip.compass');
  compassStripEl.style.cssText =
    'position:fixed;top:14px;left:50%;transform:translateX(-50%);' +
    'width:min(300px,70vw);height:30px;overflow:hidden;z-index:1000;' +
    'background:rgba(8,12,28,0.6);border-radius:8px;pointer-events:auto;' +
    'border:1px solid rgba(120,150,220,0.22);backdrop-filter:blur(8px);' +
    'box-shadow:0 5px 18px rgba(0,0,0,0.28);' +
    // Soft edge fade so marks dissolve at the rim.
    '-webkit-mask-image:linear-gradient(90deg,transparent,#000 18%,#000 82%,transparent);' +
    'mask-image:linear-gradient(90deg,transparent,#000 18%,#000 82%,transparent);';

  const compassCardinals: { az: number; label: string; major: boolean }[] = [
    { az: Math.PI, label: 'N', major: true },
    { az: (3 * Math.PI) / 4, label: 'NE', major: false },
    { az: Math.PI / 2, label: 'E', major: true },
    { az: Math.PI / 4, label: 'SE', major: false },
    { az: 0, label: 'S', major: true },
    { az: -Math.PI / 4, label: 'SW', major: false },
    { az: -Math.PI / 2, label: 'W', major: true },
    { az: (-3 * Math.PI) / 4, label: 'NW', major: false },
  ];
  for (const c of compassCardinals) {
    const mark = document.createElement('div');
    const color = c.label === 'N' ? '#ff8a6a' : c.major ? '#e8eef8' : '#8a9ab8';
    mark.style.cssText =
      'position:absolute;top:0;left:50%;height:30px;margin-left:-12px;width:24px;' +
      'display:flex;align-items:center;justify-content:center;' +
      `font:700 ${c.major ? '14' : '10'}px system-ui,sans-serif;color:${color};` +
      'will-change:transform,opacity;';
    mark.textContent = c.label;
    compassStripEl.appendChild(mark);
    compassMarks.push({ el: mark, az: c.az });
  }
  // Centre fixed tick marking your heading.
  const compassTick = document.createElement('div');
  compassTick.style.cssText =
    'position:absolute;top:0;left:50%;width:2px;height:30px;margin-left:-1px;' +
    'background:linear-gradient(#ffd24a,rgba(255,210,74,0));';
  compassStripEl.appendChild(compassTick);
  wrap.appendChild(compassStripEl);

  // ── Interaction hint (bottom-center, above controls) ─────────────────
  interactHintEl = document.createElement('div');
  interactHintEl.style.cssText =
    'position:fixed;bottom:62px;left:50%;transform:translateX(-50%) translateY(8px);' +
    'display:flex;align-items:center;gap:9px;z-index:1001;pointer-events:none;' +
    'background:linear-gradient(135deg,rgba(20,26,46,0.86),rgba(12,16,30,0.78));' +
    'color:#eef3ff;padding:9px 16px;border-radius:24px;' +
    'border:1px solid rgba(255,210,120,0.4);backdrop-filter:blur(10px);' +
    'box-shadow:0 8px 26px rgba(0,0,0,0.4);font-size:14px;font-weight:600;' +
    'opacity:0;transition:opacity 0.18s ease,transform 0.18s ease;white-space:nowrap;';

  interactKeyEl = document.createElement('span');
  interactKeyEl.style.cssText =
    'display:inline-flex;align-items:center;justify-content:center;' +
    'min-width:24px;height:24px;padding:0 5px;border-radius:6px;' +
    'background:#2a3450;' +
    'border:1px solid rgba(255,210,120,0.5);color:#ffe08a;' +
    'font:800 13px system-ui,sans-serif;box-shadow:0 2px 6px rgba(0,0,0,0.4);';
  interactKeyEl.textContent = 'F';

  interactLabelEl = document.createElement('span');
  interactLabelEl.textContent = '';

  interactHintEl.appendChild(interactKeyEl);
  interactHintEl.appendChild(interactLabelEl);
  wrap.appendChild(interactHintEl);

  document.body.appendChild(wrap);
  document.body.appendChild(winEl);

  refreshHud(state);
}

/** Apply the saved Music/SFX volume + mute settings to the audio buses. */
function initAudioBuses(): void {
  setBusVolume('music', VOLUME_LEVELS[musicVolIdx]);
  setBusVolume('sfx', VOLUME_LEVELS[sfxVolIdx]);
  setBusMuted('music', !musicOn);
  setBusMuted('sfx', !sfxOn);
}

async function bootstrap(): Promise<void> {
  // Paint the loading screen immediately — before building the runtime, parsing
  // the scene, or loading any asset — so there's no gap where the blank scene
  // or HUD shows through.
  const bootLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  mountLoadingScreen({
    title: bootLang === 'pt' ? 'Vale do Cristal' : 'Crystal Vale',
    subtitle:
      bootLang === 'pt' ? 'A preparar o mundo…' : 'Preparing the world…',
  });

  // Declare every game sound once (url + volume + bus). After this, any code
  // can `playSound('coin')` — no scene entity, no eid lookup.
  registerGameSounds();

  // J ataca como o clique esquerdo (animação + meleeHit + rochas destrutíveis).
  addInputMapping('primaryAction', 'KeyJ');

  withPlugin(LoadingPlugin);
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withPlugin(NavMeshPlugin);
  withPlugin(CombatPlugin);
  withPlugin(DebugPlugin);
  withSystem(GameplayHudSystem);
  withSystem(HeroGroundSnapSystem);
  withSystem(PostFxToggleSystem);

  configure({ canvas: '#game-canvas' });

  const builder = getBuilder();
  resetBuilder();
  const runtime = await builder.build();
  const state = runtime.getState();

  registerEntityScripts(state, import.meta.glob('./scripts/*.ts'));

  window.__heroState = state;

  // Engine DestructiblePlugin breaks the rocks (swing timing, particles);
  // the game only collects the loot — the HUD watcher then shows the
  // localized "+1 Pedra!" popup and plays the SFX.
  onDestructibleDestroyed(state, (eid, x, y, z) => {
    if (eid !== null && isWoodEntity(eid)) {
      addWood(1, x, y + 0.8, z);
      addItem('wood', 1);
      pushFloat(state, x, y + 1.5, z, '+1 🪵', { color: '#e0b87a', size: 19 });
      playSfx('chop-break');
    } else {
      addStone(1, x, y, z);
      addItem('stone', 1);
      pushFloat(state, x, y + 1.2, z, '+1 🪨', { color: '#e2dccb', size: 19 });
      playSfx('mine-break');
    }
  });

  // QA helper: spawn a floating text from the console / automated tests.
  window.__spawnFloatingText = (text, x, y, z) =>
    spawnFloatingText(state, text, { x, y, z, duration: 4 });

  window.__heroDebug = () => {
    const heroEid = state.getEntityByName('hero');
    if (heroEid === null) return {};
    const x = Transform.posX[heroEid];
    const y = Transform.posY[heroEid];
    const z = Transform.posZ[heroEid];
    const terrainY =
      getBvhSurfaceHeight(state, x, 500, z) ?? getTerrainHeightAt(state, x, z);
    const feetY = getCharacterFeetY(state, heroEid, y);
    const CM = state.getComponent('character-movement');
    const CC = state.getComponent('character-controller');
    const RB = state.getComponent('rigidbody');
    const PC = state.getComponent('player-controller');
    const IS = state.getComponent('input-state');
    const body = getBodyForEntity(state, heroEid);
    const rapierY = body?.translation().y;
    const rapierVel = body ? body.linvel() : null;
    return {
      x,
      y,
      z,
      rapierY,
      terrainY,
      feetY,
      groundGap: feetY - terrainY,
      vy: RB?.velY?.[heroEid] ?? 0,
      rapierVx: rapierVel?.x ?? 0,
      rapierVy: rapierVel?.y ?? 0,
      rapierVz: rapierVel?.z ?? 0,
      grounded: CC?.grounded?.[heroEid] ?? 0,
      desiredVelX: CM?.desiredVelX?.[heroEid] ?? 0,
      desiredVelZ: CM?.desiredVelZ?.[heroEid] ?? 0,
      speed: PC?.speed?.[heroEid] ?? -1,
      moveY: IS?.moveY?.[heroEid] ?? -1,
      moveX: IS?.moveX?.[heroEid] ?? -1,
      bodyType: RB?.type?.[heroEid] ?? -1,
    };
  };

  initAudioBuses();

  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);

  const userLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  setLocale(state, userLang);

  createOverlayHud(state);

  // Spending a Vitality point raises the hero's max HP (Strength/Agility are
  // stored for future use). Kept here so the skills module stays ECS-free.
  setSkillEffectHandler((key) => {
    if (key !== 'vitality') return;
    const hero = state.getEntityByName('hero');
    if (hero !== null && state.hasComponent(hero, Health)) {
      Health.max[hero] += 12;
      Health.current[hero] = Math.min(
        Health.max[hero],
        Health.current[hero] + 12
      );
    }
  });

  // Inventory mirrors the live resource counters (single source of truth) and
  // can hold future bag-owned items.
  registerResource('gold', '🪙', { en: 'Gold', pt: 'Ouro' }, () => getGold());
  registerResource('wood', '🪵', { en: 'Wood', pt: 'Madeira' }, () =>
    getWoodCount()
  );
  registerResource('stone', '🪨', { en: 'Stone', pt: 'Pedra' }, () =>
    getStoneCount()
  );
  defineItem('potion', '🧪', { en: 'Potion', pt: 'Poção' });

  const pauseOptions: PauseOption[] = [
    {
      labelKey: 'pause.option.language',
      value: () => (getLocale(state) === 'pt' ? 'PT' : 'EN'),
      activate: () => {
        setLocale(state, getLocale(state) === 'pt' ? 'en' : 'pt');
        if (overlayControlsEl)
          overlayControlsEl.textContent = t(state, 'hud.controls');
        refreshHud(state);
      },
    },
    {
      labelKey: 'pause.option.music',
      value: () => t(state, musicOn ? 'opt.on' : 'opt.off'),
      activate: () => {
        musicOn = !musicOn;
        setBusMuted('music', !musicOn);
        if (musicOn) setMusicPlaying(true);
      },
    },
    {
      labelKey: 'pause.option.musicVol',
      value: () => t(state, VOLUME_LABELS[musicVolIdx]),
      activate: () => {
        musicVolIdx = (musicVolIdx + 1) % VOLUME_LEVELS.length;
        setBusVolume('music', VOLUME_LEVELS[musicVolIdx]);
      },
    },
    {
      labelKey: 'pause.option.sfx',
      value: () => t(state, sfxOn ? 'opt.on' : 'opt.off'),
      activate: () => {
        sfxOn = !sfxOn;
        setBusMuted('sfx', !sfxOn);
      },
    },
    {
      labelKey: 'pause.option.sfxVol',
      value: () => t(state, VOLUME_LABELS[sfxVolIdx]),
      activate: () => {
        sfxVolIdx = (sfxVolIdx + 1) % VOLUME_LEVELS.length;
        setBusVolume('sfx', VOLUME_LEVELS[sfxVolIdx]);
      },
    },
  ];

  pauseMenu = createPauseMenu({
    state,
    translate: (k, v) => t(state, k, v),
    locale: () => (getLocale(state) === 'pt' ? 'pt' : 'en'),
    getLevel: () => level,
    onSave: () => {
      saveToLocalStorage(state, SAVE_KEY);
      playSfx('save');
      pushFlash(state, 'hud.saved', 2.5);
    },
    onLoad: () => {
      const ok = loadFromLocalStorage(state, SAVE_KEY);
      if (ok) playSfx('load');
      pushFlash(state, ok ? 'hud.loaded' : 'hud.no-save', 2.5);
    },
    options: pauseOptions,
  });

  if (typeof document !== 'undefined') {
    const startBgm = () => {
      resumeAudioContextIfSuspended();
      if (musicOn) setMusicPlaying(true);
      document.removeEventListener('pointerdown', startBgm);
    };
    document.addEventListener('pointerdown', startBgm, { once: true });
  }

  await runtime.start();
}

void bootstrap();

// HMR teardown: Vite full-reloads on every source save (no HMR boundary in the
// engine). Without this, the old WebGL context + Rapier/recast WASM + GPU memory
// are never freed before the reloaded page allocates a fresh set, so reloads
// stack VRAM and eventually lock the tab. Dispose runs renderer.setAnimationLoop(null)
// + renderer.dispose() + state.dispose() before the page reloads.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    try {
      disposeAllRuntimes();
    } catch (e) {
      console.error('[VibeGame] HMR dispose failed:', e);
    }
  });
}
