declare global {
  interface Window {
    __heroState?: State;
    __heroDebug?: () => Record<string, number>;
    __spawnFloatingText?: (
      text: string,
      x: number,
      y: number,
      z: number
    ) => number;
    __hold?: (key: string | null) => string | null;
    __grip?: (key: string, patch: Record<string, number>) => unknown;
    __cam?: (yaw: number, pitch: number, dist: number) => number;
    __handInfo?: () => string;
  }
}

import type { System, State } from 'vibegame';
import {
  configure,
  disposeAllRuntimes,
  getBuilder,
  resetBuilder,
  withPlugin,
  withSystem,
  registerEntityScripts,
  setKTX2TranscoderPath,
  // Plugins (engine RPG stack)
  LoadingPlugin,
  NavMeshPlugin,
  SaveLoadPlugin,
  I18nPlugin,
  CombatPlugin,
  DebugPlugin,
  RpgCorePlugin,
  RpgVaultPlugin,
  InventoryPlugin,
  ProgressionPlugin,
  PauseCoordinatorPlugin,
  ResourceNodePlugin,
  EconomyPlugin,
  StatusEffectsPlugin,
  RpgAiPlugin,
  SpawnGatePlugin,
  // HUD / loading
  mountLoadingScreen,
  // i18n
  loadDictionary,
  loadEngineDefaultDictionary,
  setLocale,
  // audio
  playSound,
  setBusVolume,
  setBusMuted,
  resumeAudioContextIfSuspended,
  // input
  addInputMapping,
  isKeyDown,
  setPlayerAttackClip,
  setPlayerHeldItem,
  setPlayerFaceTarget,
  PlayerGltfConfig,
  animatorRegistry,
  // ecs / gameplay
  defineQuery,
  Transform,
  WorldTransform,
  Rigidbody,
  Health,
  isDead,
  ProgressionComponent,
  InventoryComponent,
  addItem,
  getItemQty,
  removeItem,
  addXp,
  getStatModifiers,
  isPaused,
  onEvent,
  MODAL_OPTION_CHANGED,
  spawnFloatingText,
  Destructible,
  onDestructibleDestroyed,
  saveToLocalStorage,
  loadFromLocalStorage,
  getDataRegistry,
  // physics / terrain
  getBodyForEntity,
  getBvhSurfaceHeight,
  getTerrainHeightAt,
  getBodyYForFeetAt,
  getRapierWorld,
  getTerrainContext,
  isTerrainDynamicsBlocking,
  PhysicsStepSystem,
  GROUND_CONTACT_SKIN,
  threeCameras,
  ThirdPersonCamera,
  getScene,
} from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';
import { Euler, Vector3, type Camera, type Object3D, type Quaternion } from 'three';

setKTX2TranscoderPath('/libs/basis/');

import { registerGameSounds } from './game/sounds';
import { registerGameSkills } from './game/skills';
import {
  spawnBomb,
  throwBomb,
  updateBombs,
  nearestEnemy,
  updateThrowArc,
  hideThrowArc,
} from './game/bombs';
import { bindEngine } from './game/engine-bridge';
import { isWoodEntity } from './scripts/tree';
import { addStone } from './scripts/inventory';
import { addWood } from './scripts/wood';

const SAVE_KEY = 'simple-rpg-save';
const BASE_MAX_HP = 100;
const CHECKPOINT_X = 0;
const CHECKPOINT_Y = 50;
const CHECKPOINT_Z = 0;
const RESPAWN_DELAY = 2.0;

// ── Terrain-settle: freeze the hero at spawn until the heightmap is ready, then
//    snap it onto the ground exactly once. ───────────────────────────────────
function isTerrainReady(state: State): boolean {
  for (const [, data] of getTerrainContext(state)) {
    if (!data.initialized) continue;
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

    if (physicsGroundBelow(state, body, x, snapY, z)) {
      heroGroundSnapped = true;
    }
  },
};

const _downDir = { x: 0, y: -1, z: 0 };

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

// ── Hero ECS setup: add the engine components the gameplay/HUD read. ─────────
let heroInit = false;
const HeroSetupSystem: System = {
  group: 'simulation',
  first: true,
  update(state: State) {
    if (heroInit) return;
    const hero = state.getEntityByName('hero');
    if (hero === null) return;

    if (!state.hasComponent(hero, Health)) state.addComponent(hero, Health);
    Health.max[hero] = BASE_MAX_HP;
    Health.current[hero] = BASE_MAX_HP;

    if (!state.hasComponent(hero, ProgressionComponent))
      state.addComponent(hero, ProgressionComponent);
    ProgressionComponent.level[hero] = 1;

    if (!state.hasComponent(hero, InventoryComponent))
      state.addComponent(hero, InventoryComponent);

    heroInit = true;
  },
};

// ── Hero stats: max HP = base + Vitality skill modifiers (engine progression). ─
const HeroStatsSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null || !state.hasComponent(hero, ProgressionComponent))
      return;
    if (!state.hasComponent(hero, Health)) return;

    let bonus = 0;
    for (const mod of getStatModifiers(state, hero)) {
      if (mod.stat === 'maxHp') bonus += mod.magnitude;
    }
    const newMax = BASE_MAX_HP + bonus;
    if (Health.max[hero] !== newMax) {
      Health.max[hero] = newMax;
      if (Health.current[hero] > newMax) Health.current[hero] = newMax;
    }
  },
};

// ── Respawn: on death, after a delay, return the hero to the checkpoint. ──────
let deathShown = false;
let respawnAtTime = 0;
const RespawnSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null || !state.hasComponent(hero, Health)) return;

    if (isDead(hero) && !deathShown) {
      deathShown = true;
      respawnAtTime = state.time.elapsed + RESPAWN_DELAY;
    }
    if (deathShown && state.time.elapsed >= respawnAtTime) {
      Health.current[hero] = Health.max[hero];
      const body = getBodyForEntity(state, hero);
      Transform.posX[hero] = CHECKPOINT_X;
      Transform.posY[hero] = CHECKPOINT_Y;
      Transform.posZ[hero] = CHECKPOINT_Z;
      Transform.dirty[hero] = 1;
      Rigidbody.velX[hero] = 0;
      Rigidbody.velY[hero] = 0;
      Rigidbody.velZ[hero] = 0;
      if (body) {
        body.setTranslation(
          { x: CHECKPOINT_X, y: CHECKPOINT_Y, z: CHECKPOINT_Z },
          true
        );
        body.setLinvel(new RAPIER.Vector3(0, 0, 0), true);
        body.wakeUp();
      }
      heroGroundSnapped = false;
      deathShown = false;
    }
  },
};

// ── Save / load on keys (the engine pause menu's OptionsTab has no button row). ─
let savePressed = false;
let loadPressed = false;
const SaveLoadKeysSystem: System = {
  group: 'simulation',
  update(state: State) {
    const save = isKeyDown('KeyG');
    if (save && !savePressed) {
      saveToLocalStorage(state, SAVE_KEY);
      playSound('save');
    }
    savePressed = save;

    const load = isKeyDown('KeyH');
    if (load && !loadPressed) {
      if (loadFromLocalStorage(state, SAVE_KEY)) playSound('load');
    }
    loadPressed = load;
  },
};

// ── Combat & harvest feedback (game-side juice the engine doesn't own):
//    floating damage numbers + hurt/kill SFX + XP-on-kill for any Health entity,
//    and a hit spark + chop/mine SFX when a Destructible swing lands. ──────────
const healthFxQuery = defineQuery([Health, Transform]);
const destructibleFxQuery = defineQuery([Destructible, Transform]);
const prevHp = new Map<number, number>();
const prevPending = new Map<number, number>();

const CombatFeedbackSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');

    for (const e of healthFxQuery(state.world)) {
      const cur = Health.current[e];
      const prev = prevHp.get(e);
      prevHp.set(e, cur);
      if (prev === undefined || cur >= prev - 0.01) continue;
      const dmg = Math.round(prev - cur);
      if (dmg <= 0) continue;
      const isHero = e === hero;
      const big = !isHero && dmg >= 22;
      spawnFloatingText(
        state,
        isHero ? `-${dmg}` : big ? `${dmg}!` : `${dmg}`,
        {
          x: Transform.posX[e],
          y: Transform.posY[e] + (isHero ? 1.7 : 2.1),
          z: Transform.posZ[e],
          color: isHero ? '#ff5a5a' : big ? '#ff8a2a' : '#fff0a0',
          size: isHero ? 0.5 : big ? 0.7 : 0.46,
          duration: big ? 1.3 : 1.0,
        }
      );
      playSound(isHero ? 'player-hurt' : 'enemy-hurt');
      // Award XP to the hero on the blow that kills a creature.
      if (!isHero && cur <= 0 && prev > 0 && hero !== null) {
        addXp(state, hero, Math.max(2, Math.round((Health.max[e] || 30) / 12)));
      }
    }

    for (const e of destructibleFxQuery(state.world)) {
      const pend = Destructible.pendingImpact[e];
      const prev = prevPending.get(e) ?? 0;
      prevPending.set(e, pend);
      if (prev > 0 && pend <= 0) {
        const wood = isWoodEntity(e);
        spawnFloatingText(state, '✦', {
          x: Transform.posX[e],
          y: Transform.posY[e] + 1.6,
          z: Transform.posZ[e],
          color: wood ? '#9be37a' : '#e2dccb',
          size: 0.55,
          duration: 0.55,
        });
        playSound(wood ? 'chop-hit' : 'mine-hit');
      }
    }
  },
};

// ── i18n. Engine HUD widget keys (modal.pause, options.*) live alongside the
//    game's own strings. ──────────────────────────────────────────────────────
const dictEN: Record<string, string> = {
  'modal.pause': 'Paused',
  'options.music': 'Music',
  'options.sfx': 'Sound FX',
  'hud.title': 'Crystal Vale',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
};

const dictPT: Record<string, string> = {
  'modal.pause': 'Pausa',
  'options.music': 'Música',
  'options.sfx': 'Efeitos',
  'hud.title': 'Vale do Cristal',
  'hud.saved': 'Jogo gravado!',
  'hud.loaded': 'Progresso restaurado.',
};

const MUSIC_VOL = 0.7;
const SFX_VOL = 0.8;

function initAudioBuses(): void {
  setBusVolume('music', MUSIC_VOL);
  setBusVolume('sfx', SFX_VOL);
  setBusMuted('music', false);
  setBusMuted('sfx', false);
}

// ── Attack-clip context: pick the player's swing animation by what they're
//    about to hit — chop a tree, mine a rock, else the equipped weapon
//    (sword/axe/spear, cycled with [V]). The gather/pickup gesture is the F
//    interact (handled in the player system). ──────────────────────────────
const WEAPON_CLIPS = ['sword', 'axe', 'spear'] as const;
const MESH_BASE = '/assets/meshes/';
// Held model per action clip. (Generated by text3d+paint3d; sword reuses the
// existing hero sword.) Missing GLBs just leave the hand empty (load fails
// silently) until generated.
const HELD_MODEL: Record<string, string> = {
  sword: MESH_BASE + 'sword_hero.glb',
  axe: MESH_BASE + 'axe.glb',
  spear: MESH_BASE + 'spear.glb',
  chop: MESH_BASE + 'felling_axe.glb',
  mine: MESH_BASE + 'pickaxe.glb',
  bomb: MESH_BASE + 'bomb.glb',
};
const BOMB_MODEL = MESH_BASE + 'bomb.glb';
// Per-weapon grip on the RightHand bone. Each generated model has a different
// long axis (sword/axe/spear=Y, felling_axe=X, pickaxe=Z) and pivot, so each
// needs its own offset/rotation/scale. TUNE visually via window.__grip(key,…).
type Grip = {
  x: number; y: number; z: number;
  rx: number; ry: number; rz: number;
  scale: number;
};
const GRIPS: Record<string, Grip> = {
  // Tuned live in-browser (window.__hold/__grip + side camera). The hand bone's
  // local +Y points world-down at rest, so rx≈+90° swings a Y-long model to
  // point forward-down out of the fist.
  // All tuned live in-browser (window.__hold/__grip + side camera).
  sword: { x: 0.27, y: 0.04, z: 0.09, rx: -1.33, ry: 12.71, rz: 0.96, scale: 0.7 },
  axe: { x: -0.12, y: 0.36, z: -0.24, rx: -1.42, ry: 12.71, rz: Math.PI * 0.5, scale: 0.55 },
  spear: { x: -0.55, y: 0.36, z: -0.41, rx: -1.33, ry: 12.71, rz: 0.96, scale: 1.2 },
  chop: { x: -0.22, y: 0.4, z: -0.39, rx: -4.96, ry: 9.2, rz: -0.61, scale: 0.7 },
  mine: { x: -0.09, y: 0.05, z: -0.13, rx: -2.82, ry: 11.38, rz: 2.01, scale: 0.55 },
  bomb: { x: -0.06, y: -0.08, z: -0.12, rx: -2.02, ry: 15.75, rz: -1.05, scale: 0.45 },
};
const BOMB_GRIP = GRIPS.bomb;
// Debug: force-hold a weapon (or null) regardless of proximity, for grip tuning.
let forcedHold: string | null = null;

let weaponIdx = 0;
let weaponCyclePressed = false;
let bombAiming = false; // BombSystem owns the hand + facing while aiming
const HARVEST_HINT_RANGE_SQ = 3.6 * 3.6;
const AttackContextSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null) return;

    const v = isKeyDown('KeyV');
    if (v && !weaponCyclePressed) {
      weaponIdx = (weaponIdx + 1) % WEAPON_CLIPS.length;
    }
    weaponCyclePressed = v;

    const hx = Transform.posX[hero];
    const hz = Transform.posZ[hero];
    let near = 0;
    let bestD2 = HARVEST_HINT_RANGE_SQ;
    for (const e of destructibleFxQuery(state.world)) {
      const dx = Transform.posX[e] - hx;
      const dz = Transform.posZ[e] - hz;
      const d2 = dx * dx + dz * dz;
      if (d2 < bestD2) {
        bestD2 = d2;
        near = e;
      }
    }
    const clip = forcedHold
      ? forcedHold
      : near
        ? isWoodEntity(near)
          ? 'chop'
          : 'mine'
        : WEAPON_CLIPS[weaponIdx];
    setPlayerAttackClip(clip);
    // Show the matching model in hand (unless the bomb-aim owns the hand).
    if (!bombAiming) setPlayerHeldItem(HELD_MODEL[clip] ?? null, GRIPS[clip]);
  },
};

// ── Bombs: tick live fuses every frame; throw one in front of the hero on [B]
//    when a bomb is in the bag (bought from the merchant). ─────────────────────
let bombPressed = false;
let bombHoldT = 0;
const BOMB_AIM_THRESHOLD = 0.18; // s held before the throw arc shows
const BOMB_AIM_RANGE = 30; // m auto-aim search radius
const BOMB_THROW_RANGE = 10; // m forward when no enemy is in range
const _bombLand = { x: 0, y: 0, z: 0 };
const _bombFrom = { x: 0, y: 0, z: 0 };
let bombHudEl: HTMLDivElement | null = null;

function updateBombHud(count: number): void {
  if (typeof document === 'undefined') return;
  if (!bombHudEl) {
    bombHudEl = document.createElement('div');
    // Sits in the resource-chip row (top-left), after gold/wood/stone.
    bombHudEl.style.cssText =
      'position:absolute;top:108px;left:284px;z-index:12;min-width:64px;' +
      'display:inline-flex;align-items:center;justify-content:center;gap:7px;' +
      'padding:7px 13px;border-radius:10px;font:700 14px system-ui,sans-serif;' +
      'color:#ff8a6a;border:1px solid rgba(255,120,80,0.3);' +
      'background:linear-gradient(135deg,rgba(14,18,34,0.72),rgba(10,14,26,0.6));' +
      'backdrop-filter:blur(10px);box-shadow:0 5px 18px rgba(0,0,0,0.25);';
    const layer =
      document.querySelector('.vibe-hud-screen-layer') ?? document.body;
    layer.appendChild(bombHudEl);
  }
  bombHudEl.textContent = `💣 ${count}`;
  bombHudEl.style.display = count > 0 ? 'inline-flex' : 'none';
}

const BombSystem: System = {
  group: 'simulation',
  update(state: State) {
    updateBombs(state, state.time.deltaTime);
    const heroForHud = state.getEntityByName('hero');
    updateBombHud(
      heroForHud !== null ? getItemQty(state, heroForHud, 'bomb') : 0
    );
    const dt = state.time.deltaTime;
    const held = isKeyDown('KeyB');
    const hero = state.getEntityByName('hero');
    const haveBomb = hero !== null && getItemQty(state, hero, 'bomb') > 0;

    if (isPaused(state) || hero === null) {
      if (bombPressed) hideThrowArc();
      if (bombAiming) {
        bombAiming = false;
        setPlayerFaceTarget(null);
      }
      bombPressed = held;
      bombHoldT = 0;
      return;
    }

    // While holding (with a bomb): aim. Resolve the landing point (auto-aim the
    // nearest enemy, else a point ahead) and draw the throw arc past the
    // threshold. The bomb is only consumed on release.
    if (held && haveBomb) {
      bombHoldT += dt;
      _bombFrom.x = Transform.posX[hero];
      _bombFrom.y = Transform.posY[hero] + 1.0;
      _bombFrom.z = Transform.posZ[hero];
      const target = nearestEnemy(state, hero, BOMB_AIM_RANGE);
      if (target) {
        _bombLand.x = Transform.posX[target];
        _bombLand.y = Transform.posY[target];
        _bombLand.z = Transform.posZ[target];
      } else {
        const rx = WorldTransform.rotX[hero];
        const ry = WorldTransform.rotY[hero];
        const rz = WorldTransform.rotZ[hero];
        const rw = WorldTransform.rotW[hero];
        const fx = 2 * (rx * rz + rw * ry);
        const fz = 1 - 2 * (rx * rx + ry * ry);
        _bombLand.x = Transform.posX[hero] + fx * BOMB_THROW_RANGE;
        _bombLand.z = Transform.posZ[hero] + fz * BOMB_THROW_RANGE;
        let gy = getBvhSurfaceHeight(state, _bombLand.x, 500, _bombLand.z);
        if (gy == null || !Number.isFinite(gy))
          gy = getTerrainHeightAt(state, _bombLand.x, _bombLand.z);
        _bombLand.y = Number.isFinite(gy) ? gy : Transform.posY[hero];
      }
      if (bombHoldT > BOMB_AIM_THRESHOLD) {
        updateThrowArc(
          state,
          _bombFrom.x,
          _bombFrom.y,
          _bombFrom.z,
          _bombLand.x,
          _bombLand.y,
          _bombLand.z
        );
        // Bomb to hand + turn the body to face the throw target while aiming.
        if (!bombAiming) {
          bombAiming = true;
          setPlayerHeldItem(BOMB_MODEL, BOMB_GRIP);
        }
        setPlayerFaceTarget(_bombLand.x, _bombLand.z);
      }
    }

    // Release: tap = drop at feet; held (aimed) = lob along the arc.
    if (!held && bombPressed) {
      hideThrowArc();
      if (bombAiming) {
        bombAiming = false;
        setPlayerFaceTarget(null); // AttackContextSystem restores the weapon
      }
      if (haveBomb) {
        if (bombHoldT <= BOMB_AIM_THRESHOLD) {
          spawnBomb(
            state,
            Transform.posX[hero],
            Transform.posY[hero],
            Transform.posZ[hero],
            hero
          );
        } else {
          throwBomb(
            state,
            _bombFrom.x,
            _bombFrom.y,
            _bombFrom.z,
            _bombLand.x,
            _bombLand.y,
            _bombLand.z,
            hero
          );
        }
        removeItem(state, hero, 'bomb', 1);
      }
      bombHoldT = 0;
    }
    bombPressed = held;
  },
};

// ── Procedural bomb-aim torso twist: rotate the hero's Spine on Y to track the
//    camera yaw while aiming. Must run in 'draw' (after 'simulation', where the
//    engine ticks the AnimationMixer) so the override lands on top of the mixer.
const MAX_SPINE_TWIST = 0.9; // rad (~51°) clamp for a believable torso twist
const AIM_TWIST_RATE = 14; // 1/s exponential smoothing for aim-in / aim-out
let aimSpineActive = false;
let aimSpineBaseY = 0;
let aimSpineDelta = 0;
let cachedSpineBone: Object3D | null = null;

function findAimSpineBone(root: Object3D): Object3D | null {
  // Engine convention is plain names (cf. 'RightHand' in gltf-systems.ts), but
  // also accept the 'mixamorig:Spine' suffix style.
  for (const name of ['Spine', 'UpperChest', 'Chest']) {
    const hit = root.getObjectByName(name);
    if (hit) return hit;
  }
  let fallback: Object3D | null = null;
  root.traverse((o) => {
    if (fallback) return;
    if (
      o.name.endsWith(':Spine') ||
      o.name.endsWith(':UpperChest') ||
      o.name.endsWith(':Chest')
    ) {
      fallback = o;
    }
  });
  return fallback;
}

function getActiveCamera(): Camera | undefined {
  for (const cam of threeCameras.values()) return cam;
  return undefined;
}

const _aimEuler = new Euler(0, 0, 0, 'YXZ');

function yawFromQuaternion(q: Quaternion): number {
  _aimEuler.setFromQuaternion(q, 'YXZ');
  return _aimEuler.y;
}

function normalizeAngle(a: number): number {
  let v = a;
  while (v > Math.PI) v -= 2 * Math.PI;
  while (v < -Math.PI) v += 2 * Math.PI;
  return v;
}

const BombAimSpineSystem: System = {
  group: 'draw',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null) return;
    const regIdx = PlayerGltfConfig.animatorRegistryIndex[hero];
    if (regIdx === 0) return;
    const animator = animatorRegistry.get(regIdx);
    if (!animator) return;

    if (!cachedSpineBone || cachedSpineBone.parent === null) {
      cachedSpineBone = findAimSpineBone(animator.root);
    }
    const spine = cachedSpineBone;
    if (!spine) return;

    const k = 1 - Math.exp(-AIM_TWIST_RATE * state.time.deltaTime);

    if (bombAiming) {
      if (!aimSpineActive) {
        aimSpineActive = true;
        aimSpineBaseY = spine.rotation.y;
        aimSpineDelta = 0;
      }
      const cam = getActiveCamera();
      if (cam) {
        const heroYaw = yawFromQuaternion(animator.root.quaternion);
        const camYaw = yawFromQuaternion(cam.quaternion);
        const delta = Math.max(
          -MAX_SPINE_TWIST,
          Math.min(MAX_SPINE_TWIST, normalizeAngle(camYaw - heroYaw))
        );
        aimSpineDelta += (delta - aimSpineDelta) * k;
        spine.rotation.y = aimSpineBaseY + aimSpineDelta;
      }
      return;
    }

    if (aimSpineActive) {
      aimSpineDelta += (0 - aimSpineDelta) * k;
      spine.rotation.y = aimSpineBaseY + aimSpineDelta;
      if (Math.abs(aimSpineDelta) < 0.001) {
        aimSpineDelta = 0;
        spine.rotation.y = aimSpineBaseY;
        aimSpineActive = false;
      }
    }
  },
};

async function bootstrap(): Promise<void> {
  const bootLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  mountLoadingScreen({
    title: bootLang === 'pt' ? 'Vale do Cristal' : 'Crystal Vale',
    subtitle:
      bootLang === 'pt' ? 'A preparar o mundo…' : 'Preparing the world…',
  });

  registerGameSounds();
  addInputMapping('primaryAction', 'KeyJ');

  withPlugin(LoadingPlugin);
  withPlugin(RpgCorePlugin);
  withPlugin(RpgVaultPlugin);
  withPlugin(InventoryPlugin);
  withPlugin(ProgressionPlugin);
  withPlugin(PauseCoordinatorPlugin);
  withPlugin(ResourceNodePlugin);
  withPlugin(EconomyPlugin);
  withPlugin(CombatPlugin);
  withPlugin(StatusEffectsPlugin);
  withPlugin(RpgAiPlugin);
  withPlugin(SpawnGatePlugin);
  withPlugin(NavMeshPlugin);
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withPlugin(DebugPlugin);

  withSystem(HeroSetupSystem);
  withSystem(HeroGroundSnapSystem);
  withSystem(HeroStatsSystem);
  withSystem(RespawnSystem);
  withSystem(SaveLoadKeysSystem);
  withSystem(CombatFeedbackSystem);
  withSystem(AttackContextSystem);
  withSystem(BombSystem);
  withSystem(BombAimSpineSystem);

  configure({ canvas: '#game-canvas' });

  const builder = getBuilder();
  resetBuilder();
  const runtime = await builder.build();
  const state = runtime.getState();

  bindEngine(state);
  registerEntityScripts(state, import.meta.glob('./scripts/*.ts'));
  registerGameSkills(state);

  // Item definitions — without a registered ItemDef the inventory caps every
  // item's stack at 1, so bought bombs never accumulated. Stack them high.
  const itemReg = getDataRegistry(state);
  for (const [id, name, icon] of [
    ['bomb', 'Bomb', '💣'],
    ['wood', 'Wood', '🪵'],
    ['stone', 'Stone', '🪨'],
    ['potion', 'Potion', '🧪'],
  ] as const) {
    itemReg.register('item', id, { id, name, icon, maxStack: 99, tags: [] });
  }

  // Load data-driven RPG presets (boss/goblin/slime) into the DataRegistry
  // before runtime.start() parses the scene.
  const dataRegistry = getDataRegistry(state);
  for (const name of ['boss', 'goblin', 'slime']) {
    try {
      const res = await fetch(`/data/ai/${name}.yaml`);
      if (res.ok) dataRegistry.loadYaml(await res.text());
    } catch (err) {
      console.warn(`[simple-rpg] failed to load AI preset ${name}:`, err);
    }
  }

  loadEngineDefaultDictionary(state);
  loadDictionary(state, 'en', dictEN);
  loadDictionary(state, 'pt', dictPT);
  setLocale(state, bootLang);

  // Harvest loot: the engine DestructiblePlugin breaks rocks/trees; the game
  // banks the yield into the hero vault + bag and pops a floating "+1".
  onDestructibleDestroyed(state, (eid, x, y, z) => {
    const hero = state.getEntityByName('hero') ?? 0;
    if (eid !== null && isWoodEntity(eid)) {
      addWood(1);
      addItem(state, hero, 'wood', 1);
      spawnFloatingText(state, '+1 🪵', { x, y: y + 1.5, z, duration: 1.4 });
      playSound('chop-break');
    } else {
      addStone(1);
      addItem(state, hero, 'stone', 1);
      spawnFloatingText(state, '+1 🪨', { x, y: y + 1.2, z, duration: 1.4 });
      playSound('mine-break');
    }
  });

  // Engine OptionsTab sliders → audio buses.
  onEvent(state, MODAL_OPTION_CHANGED, (payload) => {
    const p = payload as { id: string; value: number };
    if (p.id === 'music-volume') setBusVolume('music', p.value / 100);
    else if (p.id === 'sfx-volume') setBusVolume('sfx', p.value / 100);
  });

  initAudioBuses();

  // QA / debug bridges.
  window.__heroState = state;
  window.__spawnFloatingText = (text, x, y, z) =>
    spawnFloatingText(state, text, { x, y, z, duration: 4 });
  window.__heroDebug = (): Record<string, number> => {
    const hero = state.getEntityByName('hero');
    if (hero === null) return {};
    return {
      x: Transform.posX[hero],
      y: Transform.posY[hero],
      z: Transform.posZ[hero],
      hp: Health.current[hero] ?? 0,
      maxHp: Health.max[hero] ?? 0,
      level: ProgressionComponent.level[hero] ?? 0,
    };
  };
  // Grip-tuning bridge: __hold('sword'|'axe'|'spear'|'chop'|'mine'|'bomb'|null)
  // pins a weapon in the hand; __grip(key, {scale, rz, …}) live-edits its grip.
  window.__hold = (key: string | null) => {
    forcedHold = key;
    return key;
  };
  window.__grip = (key: string, patch: Record<string, number>) => {
    if (GRIPS[key]) Object.assign(GRIPS[key], patch);
    return GRIPS[key];
  };
  // Camera orbit for grip tuning: __cam(yawRad, pitchRad, distance).
  const camQuery = defineQuery([ThirdPersonCamera]);
  window.__cam = (yaw: number, pitch: number, dist: number) => {
    for (const e of camQuery(state.world)) {
      ThirdPersonCamera.yaw[e] = yaw;
      ThirdPersonCamera.smoothYaw[e] = yaw;
      ThirdPersonCamera.pitch[e] = pitch;
      ThirdPersonCamera.distance[e] = dist;
      return e;
    }
    return -1;
  };
  // Inspect what's attached to the RightHand bone (debug grip issues).
  window.__handInfo = () => {
    const scene = getScene(state);
    const hands: { childCount: number; childNames: string[]; worldScale: number }[] = [];
    const _v = new Vector3();
    scene?.traverse((o: any) => {
      if (o.name === 'RightHand') {
        o.getWorldScale?.(_v);
        hands.push({
          childCount: o.children.length,
          childNames: o.children.map((c: any) => `${c.name}(s${(c.scale?.x ?? 0).toFixed(2)})`),
          worldScale: +_v.x.toFixed(3),
        });
      }
    });
    return JSON.stringify(hands);
  };

  if (typeof document !== 'undefined') {
    const startBgm = () => {
      resumeAudioContextIfSuspended();
      document.removeEventListener('pointerdown', startBgm);
    };
    document.addEventListener('pointerdown', startBgm, { once: true });
  }

  await runtime.start();
}

void bootstrap();

// HMR teardown: dispose the runtime (WebGL/Rapier/recast) before Vite reloads.
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    try {
      disposeAllRuntimes();
    } catch (e) {
      console.error('[VibeGame] HMR dispose failed:', e);
    }
  });
}
