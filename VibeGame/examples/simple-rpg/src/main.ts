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

import type { System, State } from 'vibegame';
import {
  AudioSource,
  NavMeshPlugin,
  PlayerController,
  configure,
  getBuilder,
  playAudioEmitter,
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
import { Rigidbody } from '../../../src/plugins/physics/components';
import { Postprocessing } from '../../../src/plugins/postprocessing/components';
import { getRenderingContext } from '../../../src/plugins/rendering';
import { threeCameras } from '../../../src/plugins/rendering/utils';
import {
  getBodyForEntity,
  getRapierWorld,
  PhysicsStepSystem,
} from '../../../src/plugins/physics/systems';
import {
  getBodyYForFeetAt,
  getCharacterFeetY,
  GROUND_CONTACT_SKIN,
} from '../../../src/plugins/physics/character-ground.ts';
import { getTerrainHeightAt } from '../../../src/plugins/terrain/systems.ts';
import { getBvhSurfaceHeight } from '../../../src/plugins/bvh/utils.ts';
import {
  getTerrainContext,
  isTerrainDynamicsBlocking,
} from '../../../src/plugins/terrain/utils';
import { Transform } from '../../../src/plugins/transforms';
import * as RAPIER from '@dimforge/rapier3d-compat';

setKTX2TranscoderPath('/libs/basis/');
import { CombatPlugin } from '../../../src/plugins/combat/index.ts';
import { DebugPlugin } from '../../../src/plugins/debug/index.ts';
import { Health, isDead } from '../../../src/plugins/combat/components.ts';
import {
  addStone,
  getStoneCount,
  getLastCollectPosition,
} from './scripts/inventory';
import { addWood, getWoodCount } from './scripts/wood';
import { getGold } from './game/economy';
import { isWoodEntity } from './scripts/tree';
import { NavMeshAgent } from '../../../src/plugins/navmesh/index';

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
    '[W/S] move  [A/D] turn  [Space] jump  [J] attack/chop  [K] talk/trade  [L] close  [Q] save  [E] load  [I] EN/PT',
  'hud.stone': 'Stone: {count}',
  'hud.stoneCollected': '+1 Stone!',
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
    '[W/S] mover  [A/D] virar  [Espaço] saltar  [J] atacar/cortar  [K] falar/comerciar  [L] fechar  [Q] gravar  [E] carregar  [I] EN/PT',
  'hud.stone': 'Pedra: {count}',
  'hud.stoneCollected': '+1 Pedra!',
};

let overlayMissionEl: HTMLDivElement | null = null;
let overlayStatsEl: HTMLDivElement | null = null;
let overlayControlsEl: HTMLDivElement | null = null;
let healthBarFill: HTMLDivElement | null = null;
let healthBarText: HTMLSpanElement | null = null;
let damageFlashEl: HTMLDivElement | null = null;
let waveCompleteEl: HTMLDivElement | null = null;
let winEl: HTMLDivElement | null = null;
let waveTopEl: HTMLDivElement | null = null;
let hudRootEl: HTMLDivElement | null = null;
let stoneCountEl: HTMLDivElement | null = null;
let goldCountEl: HTMLDivElement | null = null;
let woodCountEl: HTMLDivElement | null = null;
let bossBarEl: HTMLDivElement | null = null;
let bossBarFill: HTMLDivElement | null = null;
let bossBarText: HTMLSpanElement | null = null;
let deathEl: HTMLDivElement | null = null;
let hudRevealed = false;

let eidSfxJump = -1;
let eidSfxSave = -1;
let eidSfxLoad = -1;
let eidSfxHeal = -1;

let saveDebounce = false;
let loadDebounce = false;
let localeDebounce = false;
let prevHeroIsJumping = 0;

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

const DAMAGE_NUMBER_POOL_SIZE = 10;
const damageNumberPool: HTMLDivElement[] = [];
let damageNumberIndex = 0;

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
  if (overlayStatsEl) {
    overlayStatsEl.innerHTML =
      `${t(state, 'hud.enemies', { enemies: String(countAliveCreatures(state)) })}<br/>` +
      `${t(state, 'hud.time', { time: formatTime(playTimeSec) })}<br/>` +
      `${t(state, 'hud.locale', { lang: getLocale(state) === 'pt' ? 'PT' : 'EN' })}`;
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

function showDamageNumber(
  amount: number,
  screenX: number,
  screenY: number
): void {
  if (damageNumberPool.length === 0) return;
  const el = damageNumberPool[damageNumberIndex % DAMAGE_NUMBER_POOL_SIZE];
  damageNumberIndex++;
  el.textContent = `-${amount}`;
  el.style.left = `${screenX}px`;
  el.style.top = `${screenY}px`;
  el.style.opacity = '1';
  el.style.transform = 'translateY(0)';
  el.style.transition = 'none';
  void el.offsetHeight;
  el.style.transition = 'opacity 1s ease-out, transform 1s ease-out';
  el.style.opacity = '0';
  el.style.transform = 'translateY(-40px)';
}

function showStoneNumber(state: State): void {
  // Real 3D-space popup (FloatingTextPlugin / troika-three-text): spawned at
  // the rock, billboarded to the camera, rises and fades on its own.
  const pos = getLastCollectPosition();
  spawnFloatingText(state, t(state, 'hud.stoneCollected'), {
    x: pos.x,
    y: pos.y + 0.6,
    z: pos.z,
    color: 0xd4c9a8,
    size: 0.45,
    duration: 1.8,
    riseSpeed: 1.1,
  });
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

      if (currentHp < prevPlayerHp && prevPlayerHp > 0) {
        const dmg = Math.round(prevPlayerHp - currentHp);
        if (damageFlashEl) {
          damageFlashEl.style.transition = 'none';
          damageFlashEl.style.opacity = '1';
          void damageFlashEl.offsetHeight;
          damageFlashEl.style.transition = 'opacity 0.2s ease-out';
          damageFlashEl.style.opacity = '0';
        }
        const TransformCmp = state.getComponent('transform');
        if (TransformCmp) {
          const px = TransformCmp.posX[heroEid];
          const py = TransformCmp.posY[heroEid];
          const pz = TransformCmp.posZ[heroEid];
          const projected = project3Dto2D(px, py + 1.5, pz, state);
          if (projected) {
            showDamageNumber(dmg, projected.x, projected.y);
          }
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

    if (waveTopEl) {
      waveTopEl.textContent = t(state, 'hud.enemies', {
        enemies: String(countAliveCreatures(state)),
      });
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
      stoneCountEl.textContent = t(state, 'hud.stone', {
        count: String(currentStone),
      });
    }
    if (goldCountEl) {
      goldCountEl.textContent = `Gold: ${getGold()}`;
    }
    if (woodCountEl) {
      woodCountEl.textContent = `Wood: ${getWoodCount()}`;
    }
    const collectPos = getLastCollectPosition();
    if (collectPos.version !== prevStoneCollectVersion) {
      prevStoneCollectVersion = collectPos.version;
      showStoneNumber(state);
      if (eidSfxHeal >= 0) playAudioEmitter(state, eidSfxHeal);
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

    if (
      heroEid !== null &&
      eidSfxJump >= 0 &&
      state.hasComponent(heroEid, PlayerController)
    ) {
      const jumping = PlayerController.isJumping[heroEid];
      if (jumping === 1 && prevHeroIsJumping === 0) {
        playAudioEmitter(state, eidSfxJump);
      }
      prevHeroIsJumping = jumping;
    }

    // L agora é "cancelar/voltar" (fecha o diálogo do mercador); o idioma fica no I.
    if (isKeyDown('KeyI') && !localeDebounce) {
      localeDebounce = true;
      const next = getLocale(state) === 'pt' ? 'en' : 'pt';
      setLocale(state, next);
      if (overlayControlsEl)
        overlayControlsEl.textContent = t(state, 'hud.controls');
      refreshHud(state);
    }
    if (!isKeyDown('KeyI')) localeDebounce = false;

    if (isKeyDown('KeyQ') && !saveDebounce) {
      saveDebounce = true;
      saveToLocalStorage(state, SAVE_KEY);
      if (eidSfxSave >= 0) playAudioEmitter(state, eidSfxSave);
      pushFlash(state, 'hud.saved', 2.5);
    }
    if (!isKeyDown('KeyQ')) saveDebounce = false;

    if (isKeyDown('KeyE') && !loadDebounce) {
      loadDebounce = true;
      const ok = loadFromLocalStorage(state, SAVE_KEY);
      if (ok && eidSfxLoad >= 0) playAudioEmitter(state, eidSfxLoad);
      pushFlash(state, ok ? 'hud.loaded' : 'hud.no-save', 2.5);
    }
    if (!isKeyDown('KeyE')) loadDebounce = false;
  },
};

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
  healthBarContainer.style.cssText =
    'background:rgba(8,12,28,0.55);border-radius:10px;padding:10px 14px;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);';

  const healthBarOuter = document.createElement('div');
  healthBarOuter.style.cssText =
    'width:200px;height:22px;background:rgba(60,20,20,0.6);border-radius:4px;' +
    'position:relative;overflow:hidden;';

  healthBarFill = document.createElement('div');
  healthBarFill.style.cssText =
    'width:100%;height:100%;background:linear-gradient(90deg,#2ecc40,#5ee870);' +
    'border-radius:4px;transition:width 0.15s ease-out;';

  healthBarText = document.createElement('span');
  healthBarText.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'color:#fff;font-size:11px;font-weight:600;text-shadow:0 1px 3px rgba(0,0,0,0.6);';
  healthBarText.textContent = t(state, 'hud.hp', { hp: '100', max: '100' });

  healthBarOuter.appendChild(healthBarFill);
  healthBarOuter.appendChild(healthBarText);
  healthBarContainer.appendChild(healthBarOuter);

  overlayStatsEl = document.createElement('div');
  overlayStatsEl.style.cssText =
    'background:rgba(8,12,28,0.55);color:#b8c8e8;padding:10px 16px;' +
    'border-radius:8px;font-size:12px;line-height:1.5;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);';

  topLeft.appendChild(overlayMissionEl);
  topLeft.appendChild(healthBarContainer);

  goldCountEl = document.createElement('div');
  goldCountEl.style.cssText =
    'background:rgba(8,12,28,0.55);border-radius:8px;padding:8px 14px;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);' +
    'color:#ffd700;font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;';
  goldCountEl.textContent = 'Gold: 0';

  woodCountEl = document.createElement('div');
  woodCountEl.style.cssText =
    'background:rgba(8,12,28,0.55);border-radius:8px;padding:8px 14px;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);' +
    'color:#d4a76a;font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;';
  woodCountEl.textContent = 'Wood: 0';

  stoneCountEl = document.createElement('div');
  stoneCountEl.style.cssText =
    'background:rgba(8,12,28,0.55);border-radius:8px;padding:8px 14px;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(8px);' +
    'color:#d4c9a8;font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px;';
  stoneCountEl.textContent = t(state, 'hud.stone', { count: '0' });

  topLeft.appendChild(goldCountEl);
  topLeft.appendChild(woodCountEl);
  topLeft.appendChild(stoneCountEl);
  topLeft.appendChild(overlayStatsEl);

  damageFlashEl = document.createElement('div');
  damageFlashEl.style.cssText =
    'position:fixed;inset:0;pointer-events:none;background:rgba(255,0,0,0.35);opacity:0;z-index:1001;';

  for (let i = 0; i < DAMAGE_NUMBER_POOL_SIZE; i++) {
    const numEl = document.createElement('div');
    numEl.style.cssText =
      'position:absolute;pointer-events:none;color:#ff4444;font-size:18px;' +
      'font-weight:700;text-shadow:0 1px 4px rgba(0,0,0,0.7);opacity:0;z-index:1002;';
    wrap.appendChild(numEl);
    damageNumberPool.push(numEl);
  }

  waveCompleteEl = document.createElement('div');
  waveCompleteEl.style.cssText =
    'position:fixed;top:30%;left:50%;transform:translateX(-50%);' +
    'color:#fff;font-size:28px;font-weight:700;letter-spacing:1px;' +
    'text-shadow:0 2px 12px rgba(0,0,0,0.6);opacity:0;z-index:1003;' +
    'pointer-events:none;white-space:nowrap;';

  winEl = document.createElement('div');
  winEl.style.cssText =
    'position:fixed;inset:0;z-index:2000;background:rgba(0,0,0,0.82);' +
    'display:none;flex-direction:column;align-items:center;justify-content:center;' +
    'pointer-events:auto;font-family:system-ui,Segoe UI,sans-serif;';

  const winTitle = document.createElement('div');
  winTitle.style.cssText =
    'color:#ffd700;font-size:52px;font-weight:800;letter-spacing:3px;margin-bottom:16px;' +
    'text-shadow:0 2px 16px rgba(255,200,0,0.4);';
  winTitle.textContent = 'VICTORY!';

  const winSub = document.createElement('div');
  winSub.style.cssText =
    'color:#e8eef8;font-size:22px;margin-bottom:32px;text-align:center;';
  winSub.textContent = 'You defeated the Boss Ogre!';

  const winBtn = document.createElement('button');
  winBtn.style.cssText =
    'background:rgba(120,90,30,0.35);color:#ffe08a;border:1px solid rgba(255,210,120,0.45);' +
    'padding:12px 36px;border-radius:8px;font-size:16px;cursor:pointer;pointer-events:auto;' +
    'font-family:system-ui,Segoe UI,sans-serif;';
  winBtn.textContent = 'Play Again';
  winBtn.addEventListener('click', () => location.reload());

  winEl.appendChild(winTitle);
  winEl.appendChild(winSub);
  winEl.appendChild(winBtn);

  deathEl = document.createElement('div');
  deathEl.style.cssText =
    'position:fixed;top:35%;left:50%;transform:translate(-50%,-50%);z-index:1900;' +
    'background:rgba(40,0,0,0.82);color:#ff6060;padding:24px 48px;border-radius:12px;' +
    'font:700 26px system-ui,Segoe UI,sans-serif;letter-spacing:1px;' +
    'border:1px solid rgba(255,60,60,0.4);box-shadow:0 10px 40px rgba(0,0,0,0.5);' +
    'pointer-events:none;display:none;text-align:center;';
  deathEl.textContent = 'You Died — Respawning...';

  bossBarEl = document.createElement('div');
  bossBarEl.style.cssText =
    'position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:1000;' +
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

  waveTopEl = document.createElement('div');
  waveTopEl.style.cssText =
    'position:fixed;top:20px;left:50%;transform:translateX(-50%);' +
    'background:rgba(8,12,28,0.6);color:#dde4f0;padding:8px 20px;' +
    'border-radius:8px;font-size:14px;font-weight:600;letter-spacing:0.3px;' +
    'border:1px solid rgba(90,120,200,0.18);backdrop-filter:blur(8px);' +
    'pointer-events:none;z-index:1000;white-space:nowrap;';

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
  wrap.appendChild(waveCompleteEl);
  wrap.appendChild(waveTopEl);
  wrap.appendChild(bossBarEl);
  wrap.appendChild(deathEl);
  wrap.appendChild(bottom);

  // FPS counter (top-right)
  fpsEl = document.createElement('div');
  fpsEl.style.cssText =
    'position:fixed;top:20px;right:20px;' +
    'background:rgba(8,12,28,0.55);color:#aabbcc;padding:6px 12px;' +
    'border-radius:6px;font-size:12px;font-weight:600;font-family:monospace;' +
    'border:1px solid rgba(90,120,200,0.15);backdrop-filter:blur(6px);' +
    'z-index:1000;pointer-events:none;';
  fpsEl.textContent = 'FPS: --';
  wrap.appendChild(fpsEl);

  document.body.appendChild(wrap);
  document.body.appendChild(winEl);

  refreshHud(state);
}

function resolveAudioEids(state: State): void {
  eidSfxJump = state.getEntityByName('sfx-jump') ?? -1;
  eidSfxSave = state.getEntityByName('sfx-save') ?? -1;
  eidSfxLoad = state.getEntityByName('sfx-load') ?? -1;
  eidSfxHeal = state.getEntityByName('sfx-heal') ?? -1;
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
    } else {
      addStone(1, x, y, z);
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

  resolveAudioEids(state);

  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);

  const userLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  setLocale(state, userLang);

  createOverlayHud(state);

  const bgmEid = state.getEntityByName('bgm');
  if (bgmEid !== null && typeof document !== 'undefined') {
    const startBgm = () => {
      resumeAudioContextIfSuspended();
      if (state.exists(bgmEid) && state.hasComponent(bgmEid, AudioSource)) {
        AudioSource.playing[bgmEid] = 1;
      }
      document.removeEventListener('pointerdown', startBgm);
    };
    document.addEventListener('pointerdown', startBgm, { once: true });
  }

  await runtime.start();
}

void bootstrap();
