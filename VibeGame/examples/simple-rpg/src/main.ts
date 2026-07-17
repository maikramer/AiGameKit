import type { System, State, QuestDef, HeldItemGripRegistry } from 'vibegame';
import {
  configure,
  disposeAllRuntimes,
  getBuilder,
  resetBuilder,
  withPlugin,
  withPlugins,
  withSystem,
  registerEntityScripts,
  registerQuest,
  notifyResourceHarvested,
  setKTX2TranscoderPath,
  // Plugins (engine RPG stack)
  LoadingPlugin,
  NavMeshPlugin,
  SaveLoadPlugin,
  I18nPlugin,
  DebugPlugin,
  RpgPlugins,
  registerDebugAction,
  registerDebugVar,
  SpawnGatePlugin,
  ParticlesPlugin,
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
  setPlayerIdleClip,
  setPlayerHeldItem,
  setPlayerFaceTarget,
  attachHeldItem,
  loadHeldItemGrips,
  PlayerGltfConfig,
  animatorRegistry,
  // ecs / gameplay
  defineQuery,
  Transform,
  WorldTransform,
  Rigidbody,
  Health,
  isDead,
  PlayerController,
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
  spawnDamageNumber,
  setCombatTarget,
  tickCombatTarget,
  Destructible,
  onDestructibleDestroyed,
  saveToLocalStorage,
  loadFromLocalStorage,
  registerSaveSerializer,
  getDataRegistry,
  // physics / terrain
  getBodyForEntity,
  getBvhSurfaceHeight,
  getTerrainHeightAt,
  isTerrainColliderAt,
  getBodyYForFeetAt,
  getRapierWorld,
  terrainReady,
  getTerrainContext,
  isTerrainDynamicsBlocking,
  PhysicsStepSystem,
  GROUND_CONTACT_SKIN,
  threeCameras,
  ThirdPersonCamera,
  getScene,
  registerSpawnFootprint,
} from 'vibegame';
import * as RAPIER from '@dimforge/rapier3d-compat';
import {
  Euler,
  Vector3,
  type Camera,
  type Mesh,
  type Object3D,
  type Quaternion,
} from 'three';

setKTX2TranscoderPath('/libs/basis/');

import { registerGameSounds, preloadGameSounds } from './game/sounds';
import { registerGameSkills, heroStats, RING_SPEED_MULT } from './game/skills';
import { updateConsumables, clearHotbar } from './game/consumables';
import { updateAbilities, clearAbilityBar } from './game/abilities';
import { updateMelee, clearMelee } from './game/melee';
import { addGold } from './game/economy';
import {
  spawnBomb,
  throwBomb,
  updateBombs,
  nearestEnemy,
  updateThrowArc,
  hideThrowArc,
  clearBombs,
} from './game/bombs';
import { bindEngine } from './game/engine-bridge';
import { isWoodEntity } from './scripts/tree';
import { addStone } from './scripts/inventory';
import { addWood } from './scripts/wood';
import { anyCreatureAggro } from './scripts/creature';
import { getEnemyLabel } from './scripts/enemy-registry';

import darkForestQuestsData from './data/quests/dark_forest_quests.json';
import desertQuestsData from './data/quests/desert_quests.json';
import swampQuestsData from './data/quests/swamp_quests.json';
import mountainQuestsData from './data/quests/mountain_quests.json';

const SAVE_KEY = 'simple-rpg-save';
const BASE_MAX_HP = 100;
// Flat bomb-damage bonus per merchant sword-upgrade level (folded into
// heroStats.attackBonus by HeroStatsSystem; read by bombs.ts).
const SWORD_DMG_PER_LEVEL = 10;
const CHECKPOINT_X = 0;
const CHECKPOINT_Y = 50;
const CHECKPOINT_Z = 0;
const RESPAWN_DELAY = 2.0;

let heroGroundSnapped = false;
let heroSpawnY: number | null = null;

// How far below the terrain surface the hero must drop before we treat it as a
// fall-through and re-seat. Generous enough to ignore slopes/steps/CCT skin.
const VOID_FALL_MARGIN = 8;

const HeroGroundSnapSystem: System = {
  group: 'fixed',
  after: [PhysicsStepSystem],
  update(state: State) {
    const heroEid = state.getEntityByName('hero');
    if (heroEid === null || !state.hasComponent(heroEid, Transform)) return;

    // Void-recovery failsafe: even once "snapped", the kinematic CCT can walk
    // into the void if the snap was released (via the isTerrainColliderAt
    // branch below) before the chunk's Rapier collider actually existed, or if
    // a respawn landed over not-yet-built ground. If the hero has fallen well
    // below the terrain surface, re-arm the snap so it re-seats the hero
    // instead of falling forever (seen as the world greying out to a void).
    if (heroGroundSnapped) {
      if (!terrainReady(state)) return;
      const gx = Transform.posX[heroEid];
      const gz = Transform.posZ[heroEid];
      const surfaceY =
        getBvhSurfaceHeight(state, gx, 500, gz) ??
        getTerrainHeightAt(state, gx, gz);
      if (
        Number.isFinite(surfaceY) &&
        Transform.posY[heroEid] < surfaceY - VOID_FALL_MARGIN
      ) {
        heroGroundSnapped = false;
      } else {
        return;
      }
    }

    const body = getBodyForEntity(state, heroEid);
    if (!body) return;

    const x = Transform.posX[heroEid];
    const z = Transform.posZ[heroEid];

    if (!terrainReady(state)) {
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
    } else if (Number.isFinite(snapY) && isTerrainColliderAt(state, x, z)) {
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

// ── Hero stats: resolve all three progression stat-modifiers (Vitality → max
//    HP, Strength → attack damage, Agility → move speed) plus the merchant
//    ring/sword upgrades. Strength+sword feed heroStats.attackBonus (read by
//    bombs.ts); speed is owned here so the ring multiplier can't compound. ──────
let baseHeroSpeed = 0;
let baseHeroSpeedCaptured = false;
const HeroStatsSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null || !state.hasComponent(hero, ProgressionComponent))
      return;
    if (!state.hasComponent(hero, Health)) return;

    let hpBonus = 0;
    let attackBonus = 0;
    let moveBonus = 0;
    for (const mod of getStatModifiers(state, hero)) {
      if (mod.stat === 'maxHp') hpBonus += mod.magnitude;
      else if (mod.stat === 'attack') attackBonus += mod.magnitude;
      else if (mod.stat === 'moveSpeed') moveBonus += mod.magnitude;
    }
    heroStats.attackBonus =
      attackBonus + heroStats.swordLevel * SWORD_DMG_PER_LEVEL;

    const newMax = BASE_MAX_HP + hpBonus;
    if (Health.max[hero] !== newMax) {
      Health.max[hero] = newMax;
      if (Health.current[hero] > newMax) Health.current[hero] = newMax;
    }

    if (!baseHeroSpeedCaptured) {
      baseHeroSpeed = PlayerController.speed[hero];
      baseHeroSpeedCaptured = true;
    }
    const ringMult = heroStats.ringOwned ? RING_SPEED_MULT : 1;
    const targetSpeed = (baseHeroSpeed + moveBonus) * ringMult;
    if (PlayerController.speed[hero] !== targetSpeed) {
      PlayerController.speed[hero] = targetSpeed;
    }
  },
};

// ── Respawn: on death, after a delay, return the hero to the nearest checkpoint
//    — the city centre or just outside whichever cardinal gate is closest to
//    where they fell. Beats always trekking back from the city centre after
//    dying deep in a biome. Each point is just outside the wall (z/x ±39),
//    short of the biome enemy bands (~45+), so respawns aren't instant re-deaths.
const RESPAWN_POINTS: ReadonlyArray<readonly [number, number]> = [
  [0, 0], // city plaza
  [0, 39], // north gate (forest)
  [0, -39], // south gate (swamp)
  [39, 0], // east gate (desert)
  [-39, 0], // west gate (peaks)
];
let deathShown = false;
let respawnAtTime = 0;
let respawnX = CHECKPOINT_X;
let respawnZ = CHECKPOINT_Z;
const RespawnSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    if (hero === null || !state.hasComponent(hero, Health)) return;

    if (isDead(hero) && !deathShown) {
      deathShown = true;
      respawnAtTime = state.time.elapsed + RESPAWN_DELAY;
      // Pick the checkpoint nearest to where the hero died.
      const dx = Transform.posX[hero];
      const dz = Transform.posZ[hero];
      let best = RESPAWN_POINTS[0];
      let bestD2 = Infinity;
      for (const p of RESPAWN_POINTS) {
        const d2 = (p[0] - dx) ** 2 + (p[1] - dz) ** 2;
        if (d2 < bestD2) {
          bestD2 = d2;
          best = p;
        }
      }
      respawnX = best[0];
      respawnZ = best[1];
    }
    if (deathShown && state.time.elapsed >= respawnAtTime) {
      Health.current[hero] = Health.max[hero];
      const body = getBodyForEntity(state, hero);
      // Place the hero on the actual terrain surface at the respawn point, not
      // a hardcoded altitude — CHECKPOINT_Y (50) sits *below* most of the world
      // (plaza ground ≈ 66), so the old path dropped the hero underground and
      // relied on the snap system to pop it back up (a visible fall/jerk, and a
      // fall-through into the void whenever that chunk's collider wasn't ready).
      let respawnY = CHECKPOINT_Y;
      if (terrainReady(state)) {
        const groundY =
          getBvhSurfaceHeight(state, respawnX, 500, respawnZ) ??
          getTerrainHeightAt(state, respawnX, respawnZ);
        if (Number.isFinite(groundY)) {
          respawnY = getBodyYForFeetAt(
            state,
            hero,
            groundY + GROUND_CONTACT_SKIN
          );
        }
      }
      Transform.posX[hero] = respawnX;
      Transform.posY[hero] = respawnY;
      Transform.posZ[hero] = respawnZ;
      Transform.dirty[hero] = 1;
      Rigidbody.velX[hero] = 0;
      Rigidbody.velY[hero] = 0;
      Rigidbody.velZ[hero] = 0;
      if (body) {
        body.setTranslation({ x: respawnX, y: respawnY, z: respawnZ }, true);
        body.setLinvel(new RAPIER.Vector3(0, 0, 0), true);
        body.wakeUp();
      }
      // Still re-arm the snap so it confirms ground with physics and the
      // void-recovery failsafe owns the case where the collider is late.
      heroGroundSnapped = false;
      deathShown = false;
    }
  },
};

// Save / load now live as Save / Load buttons in the pause menu's Options tab
// (MODAL_OPTION_CHANGED handler below) — no dedicated keys.

// ── Combat & harvest feedback (game-side juice the engine doesn't own):
//    floating damage numbers + hurt/kill SFX + XP-on-kill for any Health entity,
//    and a hit spark + chop/mine SFX when a Destructible swing lands. Damage and
//    harvest feedback share a per-target vertical stack so the numbers/icons/loot
//    never pile on top of each other at the same spot. ──────────────────────────
const healthFxQuery = defineQuery([Health, Transform]);
const destructibleFxQuery = defineQuery([Destructible, Transform]);
const prevHp = new Map<number, number>();
const prevPending = new Map<number, number>();

/** Stack key shared by all feedback spawned at one prop position. */
function harvestStackKey(x: number, z: number): string {
  return `harvest@${Math.round(x)},${Math.round(z)}`;
}

const CombatFeedbackSystem: System = {
  group: 'simulation',
  update(state: State) {
    const hero = state.getEntityByName('hero');
    tickCombatTarget(state, state.time.deltaTime);

    for (const e of healthFxQuery(state.world)) {
      const cur = Health.current[e];
      const prev = prevHp.get(e);
      prevHp.set(e, cur);
      if (prev === undefined || cur >= prev - 0.01) continue;
      const dmg = Math.round(prev - cur);
      if (dmg <= 0) continue;
      const isHero = e === hero;
      const big = !isHero && dmg >= 22;
      spawnDamageNumber(state, {
        x: Transform.posX[e],
        y: Transform.posY[e] + (isHero ? 1.7 : 2.1),
        z: Transform.posZ[e],
        amount: dmg,
        onHero: isHero,
        crit: big,
        stackKey: `dmg@${e}`,
      });
      playSound(isHero ? 'player-hurt' : 'enemy-hurt');
      if (!isHero) {
        setCombatTarget(e, {
          label: getEnemyLabel(e) || state.getEntityName(e) || 'Enemy',
        });
      } else if (hero !== null) {
        // When the hero is hit, soft-lock the nearest living foe for the TargetBar.
        let best = -1;
        let bestD2 = Infinity;
        const hx = Transform.posX[hero];
        const hz = Transform.posZ[hero];
        const merchant = state.getEntityByName('merchant');
        for (const foe of healthFxQuery(state.world)) {
          if (foe === hero || foe === merchant || Health.current[foe] <= 0)
            continue;
          const dx = Transform.posX[foe] - hx;
          const dz = Transform.posZ[foe] - hz;
          const d2 = dx * dx + dz * dz;
          if (d2 < bestD2 && d2 < 400) {
            bestD2 = d2;
            best = foe;
          }
        }
        if (best >= 0) {
          setCombatTarget(best, {
            label: getEnemyLabel(best) || state.getEntityName(best) || 'Enemy',
          });
        }
      }
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
        // Hit feedback is the crack overlay + shake + woodchip/shard burst +
        // SFX (all driven by the engine destructible plugin). No floating text
        // here — a lone '*' reads as a square box and the popup/loot already
        // stack on break.
        playSound(isWoodEntity(e) ? 'chop-hit' : 'mine-hit');
      }
    }
  },
};

// ── i18n. Engine HUD widget keys (modal.pause, options.*) live alongside the
//    game's own strings. ──────────────────────────────────────────────────────
const dictEN: Record<string, string> = {
  'modal.pause': 'Paused',
  'modal.hint': 'Press Q to resume',
  'modal.tab.skills': 'Skills',
  'modal.tab.inventory': 'Inventory',
  'modal.tab.options': 'Options',
  'modal.tab.system': 'System',
  'modal.tab.quests': 'Quests',
  'modal.tab.wiki': 'Wiki',
  'modal.skillPoints': '{n} skill points',
  'modal.skillRequires': 'Requires: {names}',
  'modal.inventoryEmpty': 'Bag is empty',
  'modal.inventorySelect': 'Select an item',
  'modal.inventoryNoDesc': 'No description.',
  'modal.wikiEmpty': 'No entries yet.',
  'modal.wikiGeneral': 'General',
  'quests.active': 'Active',
  'quests.completed': 'Completed',
  'quests.failed': 'Failed',
  'options.music': 'Music',
  'options.sfx': 'Sound FX',
  'options.save': '💾 Save Game',
  'options.load': '📂 Load Game',
  'options.controls':
    'Move: WASD   Jump: Space   Sprint: Shift\n' +
    'Attack / Harvest: J   Interact: F   Trade: K\n' +
    'Bomb: B (hold to aim)   Cycle weapon: V\n' +
    'Use potion: 1   Use antidote: 2\n' +
    'Dash: C   Heal: E   Power Strike: R\n' +
    'Pause menu: Q',
  'hud.title': 'Crystal Vale',
  'hud.saved': 'Game saved!',
  'hud.loaded': 'Save restored.',
};

const dictPT: Record<string, string> = {
  'modal.pause': 'Pausa',
  'modal.hint': 'Aperte Q para voltar',
  'modal.tab.skills': 'Habilidades',
  'modal.tab.inventory': 'Inventário',
  'modal.tab.options': 'Opções',
  'modal.tab.system': 'Sistema',
  'modal.tab.quests': 'Missões',
  'modal.tab.wiki': 'Wiki',
  'modal.skillPoints': '{n} pontos de habilidade',
  'modal.skillRequires': 'Requer: {names}',
  'modal.inventoryEmpty': 'Mochila vazia',
  'modal.inventorySelect': 'Selecione um item',
  'modal.inventoryNoDesc': 'Sem descrição.',
  'modal.wikiEmpty': 'Nenhuma entrada ainda.',
  'modal.wikiGeneral': 'Geral',
  'quests.active': 'Ativas',
  'quests.completed': 'Completas',
  'quests.failed': 'Falhadas',
  'options.music': 'Música',
  'options.sfx': 'Efeitos',
  'options.save': '💾 Salvar Jogo',
  'options.load': '📂 Carregar Jogo',
  'options.controls':
    'Mover: WASD   Pular: Espaço   Correr: Shift\n' +
    'Atacar / Coletar: J   Interagir: F   Comércio: K\n' +
    'Bomba: B (segure p/ mirar)   Trocar arma: V\n' +
    'Usar poção: 1   Usar antídoto: 2\n' +
    'Investida: C   Cura: E   Golpe Forte: R\n' +
    'Menu de pausa: Q',
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
let GRIPS: HeldItemGripRegistry = {};
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
    // Combat-guard idle: when a weapon/tool is equipped, use the matching guard
    // stance (swordidle/axeidle/…) instead of the default relaxed idle. Reverts
    // to the default idle when no weapon is equipped (e.g. bomb-only).
    setPlayerIdleClip(clip ? `${clip}idle` : null);
    // Show the matching model in hand (unless the bomb-aim owns the hand).
    if (!bombAiming) {
      const url = HELD_MODEL[clip] ?? null;
      if (!attachHeldItem(state, hero, clip, GRIPS, url))
        setPlayerHeldItem(url);
    }
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
// (Bomb count now lives in the consumable hotbar — see game/consumables.ts.)

const BombSystem: System = {
  group: 'simulation',
  update(state: State) {
    updateBombs(state, state.time.deltaTime);
    const heroForHud = state.getEntityByName('hero');
    updateConsumables(state, heroForHud ?? 0);
    updateAbilities(state, heroForHud ?? 0, state.time.deltaTime);
    updateMelee(state, heroForHud ?? 0, state.time.deltaTime);
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
          attachHeldItem(state, hero, 'bomb', GRIPS, BOMB_MODEL);
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

// ── BGM: explore music on boot, switch to battle when creatures aggro.
//    Uses Howler playSound (not the engine MusicLayer system) so it routes
//    through the same 'music' bus as the volume slider.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let bgmCurrentLayer: 'explore' | 'battle' | null = null;
let bgmStarted = false;
let bgmLastSwitch = 0;

function switchBgm(layer: 'explore' | 'battle'): void {
  // Stop every Howl whose source is a BGM track, then start the new layer.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const howler = (window as any).Howler;
  if (howler?._howls) {
    for (const h of howler._howls) {
      if (typeof h._src === 'string' && h._src.includes('bgm_')) {
        h.stop();
        h.unload();
      }
    }
  }
  playSound(`bgm-${layer}`);
  bgmCurrentLayer = layer;
}

const BgmSystem: System = {
  group: 'simulation',
  update(_state: State) {
    // Wait for the AudioContext to be running (unlocked by the user gesture)
    // before starting BGM — Howler queues plays on a suspended context but
    // they may not resume reliably.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ctxRunning = (window as any).Howler?.ctx?.state === 'running';
    if (!bgmStarted) {
      if (!ctxRunning) return; // keep waiting for the user gesture
      bgmStarted = true;
      switchBgm('explore');
      return;
    }
    // Only switch if the layer actually changed AND at least 1s passed since
    // the last switch (prevents rapid oscillation from aggro flapping).
    const inCombat = anyCreatureAggro();
    const now = performance.now();
    if (now - bgmLastSwitch < 1000) return;
    if (inCombat && bgmCurrentLayer !== 'battle') {
      switchBgm('battle');
      bgmLastSwitch = now;
    } else if (!inCombat && bgmCurrentLayer === 'battle') {
      switchBgm('explore');
      bgmLastSwitch = now;
    }
  },
};

// Must register quests before runtime.start() so the scene parser can resolve
// each <DialogueNPC dialogue-id> to its quest index. JSON import widens
// objective.type to `string`, so bridge to the literal union via double assert.
function loadQuests(raw: unknown): readonly QuestDef[] {
  const list = (Array.isArray(raw) ? raw : [raw]) as readonly unknown[];
  return list as unknown as readonly QuestDef[];
}

async function bootstrap(): Promise<void> {
  const bootLang = navigator.language.startsWith('pt') ? 'pt' : 'en';
  mountLoadingScreen({
    title: bootLang === 'pt' ? 'Vale do Cristal' : 'Crystal Vale',
    subtitle:
      bootLang === 'pt' ? 'A preparar o mundo…' : 'Preparing the world…',
  });

  registerGameSounds();
  preloadGameSounds();
  addInputMapping('primaryAction', 'KeyJ');

  withPlugin(LoadingPlugin);
  withPlugins(...RpgPlugins);
  withPlugin(SpawnGatePlugin);
  withPlugin(ParticlesPlugin);
  withPlugin(NavMeshPlugin);
  withPlugin(SaveLoadPlugin);
  withPlugin(I18nPlugin);
  withPlugin(DebugPlugin);

  withSystem(HeroSetupSystem);
  withSystem(HeroGroundSnapSystem);
  withSystem(HeroStatsSystem);
  withSystem(RespawnSystem);
  withSystem(CombatFeedbackSystem);
  withSystem(AttackContextSystem);
  withSystem(BombSystem);
  withSystem(BombAimSpineSystem);
  withSystem(BgmSystem);

  configure({ canvas: '#game-canvas' });

  const builder = getBuilder();
  resetBuilder();
  const runtime = await builder.build();
  const state = runtime.getState();

  GRIPS = await loadHeldItemGrips('/data/held-items.json');

  // City exclusion zone — registered directly in the occupancy registry before
  // any StaticSpawner samples positions. Central walled city is at the origin
  // (matches the <SpawnExclusion at="0 0" radius="42"> in index.html).
  const villageZones: Array<[number, number, number]> = [[0, 0, 42]];
  for (const [x, z, r] of villageZones) {
    registerSpawnFootprint(state, x, z, r);
  }

  bindEngine(state);
  // Drop per-entity feedback sidecars when an entity is destroyed, so a recycled
  // eid can't inherit a stale prev-HP (which would show a phantom damage number
  // or swallow the first real hit). See [[eid-recycling-sidecars]].
  state.onDestroyAll((eid: number) => {
    prevHp.delete(eid);
    prevPending.delete(eid);
  });
  registerEntityScripts(state, import.meta.glob('./scripts/**/*.ts'));
  registerGameSkills(state);

  let questCount = 0;
  for (const data of [
    darkForestQuestsData,
    desertQuestsData,
    swampQuestsData,
    mountainQuestsData,
  ]) {
    for (const def of loadQuests(data)) {
      registerQuest(state, def);
      questCount++;
    }
  }
  console.info(`[simple-rpg] Loaded ${questCount} quests`);

  // Persist merchant progress that lives outside ECS (heroStats.ringOwned /
  // swordLevel) so re-loading can't re-grant the ring (speed compounding) or
  // reset sword levels. Attached to the hero entity; other eids are skipped.
  registerSaveSerializer(state, 'simple-rpg-progress', {
    serialize: (s, eid) => {
      if (s.getEntityByName('hero') !== eid) return null;
      return {
        ringOwned: heroStats.ringOwned,
        swordLevel: heroStats.swordLevel,
      };
    },
    deserialize: (s, eid, data) => {
      if (s.getEntityByName('hero') !== eid) return;
      const d = data as { ringOwned?: boolean; swordLevel?: number };
      heroStats.ringOwned = !!d.ringOwned;
      heroStats.swordLevel = d.swordLevel ?? 0;
    },
  });

  // Item definitions — without a registered ItemDef the inventory caps every
  // item's stack at 1, so bought bombs never accumulated. Stack them high.
  const itemReg = getDataRegistry(state);
  for (const [id, name, icon, description, tags] of [
    [
      'bomb',
      'Bomb',
      '/assets/icons/item_bomb.png',
      'Throwable explosive. Hold B to aim, release to throw.',
      ['combat', 'consumable'],
    ],
    [
      'wood',
      'Wood',
      '/assets/icons/hud_wood.png',
      'Chopped timber from Crystal Vale forests. Trade or craft.',
      ['material'],
    ],
    [
      'stone',
      'Stone',
      '/assets/icons/hud_stone.png',
      'Mined rock. Useful for trade and construction goods.',
      ['material'],
    ],
    [
      'potion',
      'Potion',
      '/assets/icons/potion_health.png',
      'Restores health. Hotkey: 1.',
      ['consumable', 'heal'],
    ],
    [
      'antidote',
      'Antidote',
      '/assets/icons/item_antidote.png',
      'Clears poison. Hotkey: 2.',
      ['consumable'],
    ],
    [
      'wolf_pelt',
      'Wolf Pelt',
      '/assets/icons/wolf_pelt.png',
      'Thick fur from Dark Forest wolves. Quest trophy.',
      ['quest', 'loot'],
    ],
    [
      'cactus_fiber',
      'Cactus Fiber',
      '/assets/icons/cactus_fiber.png',
      'Tough desert fiber. Used in eastern trade routes.',
      ['quest', 'material'],
    ],
    [
      'silk_cloth',
      'Silk Cloth',
      '/assets/icons/silk_cloth.png',
      'Fine cloth spun in the swamp marshes.',
      ['quest', 'material'],
    ],
    [
      'ancient_relic',
      'Ancient Relic',
      '/assets/icons/ancient_relic.png',
      'Weathered artifact from the mountain peaks.',
      ['quest', 'relic'],
    ],
    [
      'moss_potion',
      'Moss Potion',
      '/assets/icons/moss_potion.png',
      'Swamp brew with a sharp herbal bite.',
      ['quest', 'consumable'],
    ],
    [
      'iron_axe',
      'Iron Axe',
      '/assets/icons/iron_axe.png',
      'Sturdy axe forged for mountain work.',
      ['quest', 'tool'],
    ],
    [
      'blessed_rod',
      'Blessed Rod',
      '/assets/icons/blessed_rod.png',
      'A staff marked with vale runes.',
      ['quest', 'relic'],
    ],
    [
      'nature_amulet',
      'Nature Amulet',
      '/assets/icons/nature_amulet.png',
      'Charm woven from living vines.',
      ['quest', 'relic'],
    ],
  ] as const) {
    itemReg.register('item', id, {
      id,
      name,
      icon,
      description,
      maxStack: 99,
      tags: [...tags],
    });
  }

  try {
    const wikiRes = await fetch('/data/wiki.json');
    if (wikiRes.ok) {
      const pages = (await wikiRes.json()) as Array<{
        id: string;
        title: string;
        body: string;
        category?: string;
        icon?: string;
        order?: number;
      }>;
      for (const page of pages) {
        itemReg.register('wiki', page.id, page);
      }
      console.info(`[simple-rpg] Loaded ${pages.length} wiki pages`);
    }
  } catch (err) {
    console.warn('[simple-rpg] failed to load wiki.json:', err);
  }

  // Dev cheats (vite DEV only): grant items/gold via the debug surface for testing.
  //   __VIBEGAME__.debug.callAction('give', 'potion', 3)
  //   __VIBEGAME__.debug.callAction('gold', 500)
  registerDebugAction(state, 'give', (id: string, n = 1) => {
    const h = state.getEntityByName('hero') ?? 0;
    if (h) addItem(state, h, id, n);
  });
  registerDebugAction(state, 'gold', (n = 100) => addGold(n));

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
  // banks the yield into the hero vault + bag and pops a floating "+1". The
  // loot joins the harvest stack (engine break popup + hit icon) so the three
  // texts line up instead of overlapping at the prop's origin.
  onDestructibleDestroyed(state, (eid, x, y, z) => {
    const hero = state.getEntityByName('hero');
    if (hero === null) return;
    if (eid !== null && isWoodEntity(eid)) {
      addWood(1);
      addItem(state, hero, 'wood', 1);
      notifyResourceHarvested(state, 'wood');
      spawnFloatingText(state, '+1 Wood', {
        x,
        y: y + 1.5,
        z,
        duration: 1.4,
        color: '#c8a35a',
        stackKey: harvestStackKey(x, z),
        stackBaseY: y + 1.2,
        stackGap: 0.5,
      });
      playSound('chop-break');
    } else {
      addStone(1);
      addItem(state, hero, 'stone', 1);
      notifyResourceHarvested(state, 'stone');
      spawnFloatingText(state, '+1 Stone', {
        x,
        y: y + 1.2,
        z,
        duration: 1.4,
        color: '#d8d8d2',
        stackKey: harvestStackKey(x, z),
        stackBaseY: y + 1.2,
        stackGap: 0.5,
      });
      playSound('mine-break');
    }
  });

  // Engine OptionsTab rows → audio buses + Save/Load buttons.
  onEvent(state, MODAL_OPTION_CHANGED, (payload) => {
    const p = payload as { id: string; value: number };
    if (p.id === 'music-volume') setBusVolume('music', p.value / 100);
    else if (p.id === 'sfx-volume') setBusVolume('sfx', p.value / 100);
    else if (p.id === 'save') {
      saveToLocalStorage(state, SAVE_KEY);
      playSound('save');
    } else if (p.id === 'load') {
      if (loadFromLocalStorage(state, SAVE_KEY)) playSound('load');
    }
  });

  initAudioBuses();

  // QA / debug surface (registered through the engine DebugPlugin overlay;
  // DEV-gated by the registry itself). Invoke via:
  //   __VIBEGAME__.debug.getVar('heroDebug')
  //   __VIBEGAME__.debug.callAction('spawnFloatingText', 'hi', 0, 2, 0)
  registerDebugVar(state, 'heroState', () => state);
  registerDebugVar(state, 'diagTerrainReady', () => terrainReady(state));
  registerDebugVar(state, 'diagDynamicsBlocking', () =>
    isTerrainDynamicsBlocking(state)
  );
  registerDebugVar(state, 'diagHeroSnapped', () => heroGroundSnapped);
  registerDebugVar(state, 'diagHeroSpawnY', () => heroSpawnY);
  registerDebugVar(state, 'diagSnapProbe', () => {
    const heroEid = state.getEntityByName('hero');
    if (heroEid === null) return { err: 'no hero' };
    const x = Transform.posX[heroEid];
    const z = Transform.posZ[heroEid];
    const bvh = getBvhSurfaceHeight(state, x, 500, z);
    const tH = getTerrainHeightAt(state, x, z);
    const groundY = bvh ?? tH;
    const snapY = getBodyYForFeetAt(
      state,
      heroEid,
      (groundY ?? 0) + GROUND_CONTACT_SKIN
    );
    const body = getBodyForEntity(state, heroEid);
    const ground = body
      ? physicsGroundBelow(state, body, x, snapY, z)
      : 'no body';
    return {
      x,
      z,
      bvh,
      terrainH: tH,
      groundY,
      snapY,
      bodyPresent: !!body,
      bodyY: body?.translation().y ?? null,
      physicsGroundBelow: ground,
    };
  });
  registerDebugVar(state, 'diagTerrainCtx', () => {
    const out: Record<string, unknown> = {};
    for (const [eid, data] of getTerrainContext(state)) {
      out[String(eid)] = {
        initialized: data.initialized,
        collisionReady: (data as Record<string, unknown>).collisionReady,
        hasHeightmapUrl: !!(data as Record<string, unknown>).heightmapUrl,
        samplerData:
          !!(data as Record<string, unknown>).sampler &&
          !!(data as Record<string, unknown>).sampler?.data,
      };
    }
    return out;
  });
  registerDebugAction(
    state,
    'spawnFloatingText',
    (text: string, x: number, y: number, z: number) =>
      spawnFloatingText(state, text, { x, y, z, duration: 4 })
  );
  registerDebugVar(state, 'heroDebug', () => {
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
  });
  // Grip-tuning: callAction('hold', 'sword') pins a weapon in the hand;
  // callAction('grip', 'sword', { scale: 1.2 }) live-edits its grip.
  registerDebugAction(state, 'hold', (key: string | null) => {
    forcedHold = key;
    return key;
  });
  registerDebugAction(
    state,
    'grip',
    (key: string, patch: Record<string, number>) => {
      if (GRIPS[key]) Object.assign(GRIPS[key], patch);
      return GRIPS[key];
    }
  );
  // Camera orbit for grip tuning: callAction('cam', yawRad, pitchRad, distance).
  const camQuery = defineQuery([ThirdPersonCamera]);
  registerDebugAction(
    state,
    'cam',
    (yaw: number, pitch: number, dist: number) => {
      for (const e of camQuery(state.world)) {
        ThirdPersonCamera.yaw[e] = yaw;
        ThirdPersonCamera.smoothYaw[e] = yaw;
        ThirdPersonCamera.pitch[e] = pitch;
        ThirdPersonCamera.distance[e] = dist;
        return e;
      }
      return -1;
    }
  );
  // Inspect what's attached to the RightHand bone (debug grip issues).
  registerDebugVar(state, 'handInfo', () => {
    const scene = getScene(state);
    const hands: {
      childCount: number;
      childNames: string[];
      worldScale: number;
    }[] = [];
    const _v = new Vector3();
    scene?.traverse((o: Object3D) => {
      if (o.name === 'RightHand') {
        o.getWorldScale?.(_v);
        hands.push({
          childCount: o.children.length,
          childNames: o.children.map(
            (c: Mesh) => `${c.name}(s${c.scale.x.toFixed(2)})`
          ),
          worldScale: +_v.x.toFixed(3),
        });
      }
    });
    return JSON.stringify(hands);
  });

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
      clearBombs();
      clearAbilityBar();
      clearHotbar();
      clearMelee();
      disposeAllRuntimes();
    } catch (e) {
      console.error('[VibeGame] HMR dispose failed:', e);
    }
  });
}
