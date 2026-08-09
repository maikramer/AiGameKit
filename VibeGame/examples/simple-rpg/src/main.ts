import type { System, State, QuestDef, HeldItemGripRegistry } from 'vibegame';
import {
  configure,
  getBuilder,
  releaseRuntimeGpuResources,
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
  ProfilerPlugin,
  withSpan,
  RpgPlugins,
  registerDebugAction,
  registerDebugVar,
  SpawnGatePlugin,
  ParticlesPlugin,
  // HUD / loading
  mountLoadingScreen,
  setLoadingScreenLocale,
  // audio
  playSound,
  playSoundAt,
  setMusicVolume,
  setSfxVolume,
  setBusMuted,
  createMusicLayerDriver,
  getActiveMusicLayer,
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
  getAnimator,
  // ecs / gameplay
  defineQuery,
  Transform,
  WorldTransform,
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
  spawnFloatingText,
  spawnDamageNumber,
  setCombatTarget,
  tickCombatTarget,
  Destructible,
  onDestructibleDestroyed,
  registerSaveSerializer,
  getDataRegistry,
  // physics / terrain
  getBodyForEntity,
  getBvhSurfaceHeight,
  getTerrainHeightAt,
  getBodyYForFeetAt,
  terrainReady,
  getTerrainContext,
  isTerrainDynamicsBlocking,
  GROUND_CONTACT_SKIN,
  threeCameras,
  ThirdPersonCamera,
  getScene,
  registerSpawnFootprint,
} from 'vibegame';
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
import { registerGameSkills, playerStats, RING_SPEED_MULT } from './game/skills';
import { updateConsumables, clearHotbar } from './game/consumables';
import { updateAbilities, clearAbilityBar } from './game/abilities';
import { updateMelee, clearMelee } from './game/melee';
import { addGold } from './game/economy';
import { teleportEntity } from '../../shared/src/physics';
import { setupHmrGuard } from '../../shared/src/hmr';
import { initI18n, detectLocale } from '../../shared/src/i18n';
import { wireOptions } from '../../shared/src/options';
import { registerProfilerDebug } from '../../shared/src/profiler';
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
import {
  BIOME_IDS,
  NotaSystem,
  biomeProgress,
  clearNota,
  initNota,
  notaSnapshot,
  restoreNota,
  type NotaSnapshot,
} from './game/nota';
import { isWoodEntity } from './scripts/tree';
import { addStone } from './scripts/inventory';
import { addWood } from './scripts/wood';
import { anyCreatureAggro } from './scripts/creature';
import { biomeAtPosition, getEnemyLabel } from './scripts/enemy-registry';
import { setupAggroChain } from './scripts/aggro-chain';

import darkForestQuestsData from './data/quests/dark_forest_quests.json';
import desertQuestsData from './data/quests/desert_quests.json';
import swampQuestsData from './data/quests/swamp_quests.json';
import mountainQuestsData from './data/quests/mountain_quests.json';

const SAVE_KEY = 'simple-rpg-save';
const BASE_MAX_HP = 100;
// Flat bomb-damage bonus per merchant sword-upgrade level (folded into
// playerStats.attackBonus by PlayerStatsSystem; read by bombs.ts).
const SWORD_DMG_PER_LEVEL = 10;
const CHECKPOINT_X = 0;
const CHECKPOINT_Y = 50;
const CHECKPOINT_Z = 0;
const RESPAWN_DELAY = 2.0;

// ── Player ECS setup: add the engine components the gameplay/HUD read. ─────────
let playerInit = false;
const PlayerSetupSystem: System = {
  group: 'simulation',
  first: true,
  update(state: State) {
    if (playerInit) return;
    const player = state.getEntityByName('player');
    if (player === null) return;

    if (!state.hasComponent(player, Health)) state.addComponent(player, Health);
    Health.max[player] = BASE_MAX_HP;
    Health.current[player] = BASE_MAX_HP;

    if (!state.hasComponent(player, ProgressionComponent))
      state.addComponent(player, ProgressionComponent);
    ProgressionComponent.level[player] = 1;

    if (!state.hasComponent(player, InventoryComponent))
      state.addComponent(player, InventoryComponent);

    playerInit = true;
  },
};

// ── Player stats: resolve all three progression stat-modifiers (Vitality → max
//    HP, Strength → attack damage, Agility → move speed) plus the merchant
//    ring/sword upgrades. Strength+sword feed playerStats.attackBonus (read by
//    bombs.ts); speed is owned here so the ring multiplier can't compound. ──────
let baseHeroSpeed = 0;
let baseHeroSpeedCaptured = false;
const PlayerStatsSystem: System = {
  group: 'simulation',
  update(state: State) {
    const player = state.getEntityByName('player');
    if (player === null || !state.hasComponent(player, ProgressionComponent))
      return;
    if (!state.hasComponent(player, Health)) return;

    let hpBonus = 0;
    let attackBonus = 0;
    let moveBonus = 0;
    for (const mod of getStatModifiers(state, player)) {
      if (mod.stat === 'maxHp') hpBonus += mod.magnitude;
      else if (mod.stat === 'attack') attackBonus += mod.magnitude;
      else if (mod.stat === 'moveSpeed') moveBonus += mod.magnitude;
    }
    playerStats.attackBonus =
      attackBonus + playerStats.swordLevel * SWORD_DMG_PER_LEVEL;

    const newMax = BASE_MAX_HP + hpBonus;
    if (Health.max[player] !== newMax) {
      Health.max[player] = newMax;
      if (Health.current[player] > newMax) Health.current[player] = newMax;
    }

    if (!baseHeroSpeedCaptured) {
      baseHeroSpeed = PlayerController.speed[player];
      baseHeroSpeedCaptured = true;
    }
    const ringMult = playerStats.ringOwned ? RING_SPEED_MULT : 1;
    const targetSpeed = (baseHeroSpeed + moveBonus) * ringMult;
    if (PlayerController.speed[player] !== targetSpeed) {
      PlayerController.speed[player] = targetSpeed;
    }
  },
};

// ── Respawn: on death, after a delay, return the player to the nearest checkpoint
//    — the city centre or just outside whichever cardinal gate is closest to
//    where they fell. Beats always trekking back from the city centre after
//    dying deep in a biome. Each point is just outside the wall (z/x ±50),
//    short of the biome enemy bands, so respawns aren't instant re-deaths.
const RESPAWN_POINTS: ReadonlyArray<readonly [number, number]> = [
  [0, 0], // city plaza
  [0, 50], // north gate (forest) — wall ±38 + margin
  [0, -50], // south gate (swamp)
  [50, 0], // east gate (desert)
  [-50, 0], // west gate (peaks)
];
let deathShown = false;
let respawnAtTime = 0;
let respawnX = CHECKPOINT_X;
let respawnZ = CHECKPOINT_Z;
const RespawnSystem: System = {
  group: 'simulation',
  update(state: State) {
    const player = state.getEntityByName('player');
    if (player === null || !state.hasComponent(player, Health)) return;

    if (isDead(player) && !deathShown) {
      deathShown = true;
      respawnAtTime = state.time.elapsed + RESPAWN_DELAY;
      // Pick the checkpoint nearest to where the player died.
      const dx = Transform.posX[player];
      const dz = Transform.posZ[player];
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
      Health.current[player] = Health.max[player];
      // Place the player on the actual terrain surface at the respawn point, not
      // a hardcoded altitude — CHECKPOINT_Y (50) sits *below* most of the world
      // (plaza ground ≈ 66), so the old path dropped the player underground and
      // relied on the snap system to pop it back up (a visible fall/jerk, and a
      // fall-through into the void whenever that chunk's collider wasn't ready).
      let respawnY = CHECKPOINT_Y;
      if (terrainReady(state)) {
        const terrainH = getTerrainHeightAt(state, respawnX, respawnZ);
        const bvh = getBvhSurfaceHeight(state, respawnX, 500, respawnZ);
        const groundY = Number.isFinite(terrainH)
          ? terrainH
          : Number.isFinite(bvh)
            ? (bvh as number)
            : null;
        if (groundY !== null) {
          respawnY = getBodyYForFeetAt(
            state,
            player,
            groundY + GROUND_CONTACT_SKIN
          );
        }
      }
      teleportEntity(state, player, respawnX, respawnY, respawnZ);
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

/**
 * Biome-flavoured name for a harvest, or `null` when the generic kind is all
 * there is. Quest objectives name the local material ("madeira-escura",
 * "musgo-do-pântano"), so reporting only `wood`/`stone` left those quests
 * permanently stuck at 0 — and sent the player nowhere in particular.
 */
function biomeHarvestKind(
  kind: 'wood' | 'stone',
  x: number,
  z: number
): string | null {
  const biome = biomeAtPosition(x, z);
  if (kind === 'wood' && biome === 'dark-forest') return 'dark-wood';
  if (kind === 'stone' && biome === 'swamp') return 'bog-moss';
  return null;
}

const CombatFeedbackSystem: System = {
  name: 'CombatFeedbackSystem',
  group: 'simulation',
  update(state: State) {
    withSpan('rpg/combat-feedback', () => {
      const player = state.getEntityByName('player');
      tickCombatTarget(state, state.time.deltaTime);

      for (const e of healthFxQuery(state.world)) {
        const cur = Health.current[e];
        const prev = prevHp.get(e);
        prevHp.set(e, cur);
        if (prev === undefined || cur >= prev - 0.01) continue;
        const dmg = Math.round(prev - cur);
        if (dmg <= 0) continue;
        const isHero = e === player;
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
        if (isHero) {
          playSound('player-hurt', { originEid: e });
        } else {
          playSoundAt(
            'enemy-hurt',
            Transform.posX[e],
            Transform.posY[e],
            Transform.posZ[e],
            { originEid: e }
          );
        }
        if (!isHero) {
          setCombatTarget(e, {
            label: getEnemyLabel(e) || state.getEntityName(e) || 'Enemy',
          });
        } else if (player !== null) {
          // When the player is hit, soft-lock the nearest living foe for the TargetBar.
          let best = -1;
          let bestD2 = Infinity;
          const hx = Transform.posX[player];
          const hz = Transform.posZ[player];
          const merchant = state.getEntityByName('merchant');
          for (const foe of healthFxQuery(state.world)) {
            if (foe === player || foe === merchant || Health.current[foe] <= 0)
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
              label:
                getEnemyLabel(best) || state.getEntityName(best) || 'Enemy',
            });
          }
        }
        // Award XP to the player on the blow that kills a creature.
        if (!isHero && cur <= 0 && prev > 0 && player !== null) {
          addXp(
            state,
            player,
            Math.max(2, Math.round((Health.max[e] || 30) / 12))
          );
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
          playSoundAt(
            isWoodEntity(e) ? 'chop-hit' : 'mine-hit',
            Transform.posX[e],
            Transform.posY[e],
            Transform.posZ[e],
            { originEid: e }
          );
        }
      }
    });
  },
};

// ── i18n. The modal.pause / options.* keys shared with the other examples
//    live in examples/shared (initI18n); only the game's own strings stay
//    here. ─────────────────────────────────────────────────────────────────
const dictEN: Record<string, string> = {
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
  'quests.tracker.title': 'Quests',
  'quests.track': 'Track',
  'quests.tracking': 'Tracking',
  'quests.prompt.talk': 'Talk',
  'quests.prompt.progress': 'Ask about the task',
  'quests.prompt.turnin': 'Hand in quest',
  'options.controls':
    'Move: WASD   Jump: Space   Sprint: Shift\n' +
    'Attack / Harvest: J   Interact: F   Trade: K\n' +
    'Bomb: B (hold to aim)   Cycle weapon: V\n' +
    'Use potion: 1   Use antidote: 2\n' +
    'Dash: C   Heal: E   Power Strike: R\n' +
    'Pause menu: Q\n' +
    'Profiler: P (Shift+P deep)   Debug overlay: ?   GPU stats: G',
  'hud.title': 'Discordia',
};


const dictPT: Record<string, string> = {
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
  'quests.failed': 'Fracassadas',
  'quests.tracker.title': 'Missões',
  'quests.track': 'Rastrear',
  'quests.tracking': 'Rastreando',
  'quests.prompt.talk': 'Falar',
  'quests.prompt.progress': 'Perguntar sobre a missão',
  'quests.prompt.turnin': 'Entregar missão',
  'options.controls':
    'Mover: WASD   Pular: Espaço   Correr: Shift\n' +
    'Atacar / Coletar: J   Interagir: F   Comércio: K\n' +
    'Bomba: B (segure p/ mirar)   Trocar arma: V\n' +
    'Usar poção: 1   Usar antídoto: 2\n' +
    'Investida: C   Cura: E   Golpe Forte: R\n' +
    'Menu de pausa: Q\n' +
    'Profiler: P (Shift+P deep)   Overlay debug: ?   GPU: G',
  'hud.title': 'Discordia',
};


const MUSIC_VOL = 0.7;
const SFX_VOL = 0.8;

function initAudioBuses(state: State): void {
  // Mixer API (not raw bus volume) so the MusicLayer BGM follows too.
  setMusicVolume(state, MUSIC_VOL);
  setSfxVolume(state, SFX_VOL);
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
// existing player sword.) Missing GLBs just leave the hand empty (load fails
// silently) until generated.
const HELD_MODEL: Record<string, string> = {
  sword: MESH_BASE + 'sword_hero_lod0.glb',
  axe: MESH_BASE + 'axe_lod0.glb',
  spear: MESH_BASE + 'spear_lod0.glb',
  chop: MESH_BASE + 'felling_axe_lod0.glb',
  mine: MESH_BASE + 'pickaxe_lod0.glb',
  bomb: MESH_BASE + 'bomb_lod0.glb',
};
const BOMB_MODEL = MESH_BASE + 'bomb_lod0.glb';
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
    const player = state.getEntityByName('player');
    if (player === null) return;

    const v = isKeyDown('KeyV');
    if (v && !weaponCyclePressed) {
      weaponIdx = (weaponIdx + 1) % WEAPON_CLIPS.length;
    }
    weaponCyclePressed = v;

    const hx = Transform.posX[player];
    const hz = Transform.posZ[player];
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
    // Relaxed idle while exploring — weapon *idle clips (swordidle/axeidle/…)
    // plant a combat crouch with knee sway that reads as floating/dancing feet
    // on cobble. Guard stance only when a creature is aggro'd.
    setPlayerIdleClip(anyCreatureAggro() && clip ? `${clip}idle` : null);
    // Show the matching model in hand (unless the bomb-aim owns the hand).
    if (!bombAiming) {
      const url = HELD_MODEL[clip] ?? null;
      if (!attachHeldItem(state, player, clip, GRIPS, url))
        setPlayerHeldItem(url);
    }
  },
};

// ── Bombs: tick live fuses every frame; throw one in front of the player on [B]
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
    const playerForHud = state.getEntityByName('player');
    updateConsumables(state, playerForHud ?? 0);
    updateAbilities(state, playerForHud ?? 0, state.time.deltaTime);
    updateMelee(state, playerForHud ?? 0, state.time.deltaTime);
    const dt = state.time.deltaTime;
    const held = isKeyDown('KeyB');
    const player = state.getEntityByName('player');
    const haveBomb = player !== null && getItemQty(state, player, 'bomb') > 0;

    if (isPaused(state) || player === null) {
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
      _bombFrom.x = Transform.posX[player];
      _bombFrom.y = Transform.posY[player] + 1.0;
      _bombFrom.z = Transform.posZ[player];
      const target = nearestEnemy(state, player, BOMB_AIM_RANGE);
      if (target) {
        _bombLand.x = Transform.posX[target];
        _bombLand.y = Transform.posY[target];
        _bombLand.z = Transform.posZ[target];
      } else {
        const rx = WorldTransform.rotX[player];
        const ry = WorldTransform.rotY[player];
        const rz = WorldTransform.rotZ[player];
        const rw = WorldTransform.rotW[player];
        const fx = 2 * (rx * rz + rw * ry);
        const fz = 1 - 2 * (rx * rx + ry * ry);
        _bombLand.x = Transform.posX[player] + fx * BOMB_THROW_RANGE;
        _bombLand.z = Transform.posZ[player] + fz * BOMB_THROW_RANGE;
        let gy = getBvhSurfaceHeight(state, _bombLand.x, 500, _bombLand.z);
        if (gy == null || !Number.isFinite(gy))
          gy = getTerrainHeightAt(state, _bombLand.x, _bombLand.z);
        _bombLand.y = Number.isFinite(gy) ? gy : Transform.posY[player];
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
          attachHeldItem(state, player, 'bomb', GRIPS, BOMB_MODEL);
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
            Transform.posX[player],
            Transform.posY[player],
            Transform.posZ[player],
            player
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
            player
          );
        }
        removeItem(state, player, 'bomb', 1);
      }
      bombHoldT = 0;
    }
    bombPressed = held;
  },
};

// ── Procedural bomb-aim torso twist: rotate the player's Spine on Y to track the
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
    const player = state.getEntityByName('player');
    if (player === null) return;
    const regIdx = PlayerGltfConfig.animatorRegistryIndex[player];
    if (regIdx === 0) return;
    const animator = getAnimator(state, regIdx);
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
        const playerYaw = yawFromQuaternion(animator.root.quaternion);
        const camYaw = yawFromQuaternion(cam.quaternion);
        const delta = Math.max(
          -MAX_SPINE_TWIST,
          Math.min(MAX_SPINE_TWIST, normalizeAngle(camYaw - playerYaw))
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

// ── BGM: explore on boot, crossfade to battle while creatures are aggro.
//    Driven by the engine MusicLayerDriver against the <MusicLayer> entities
//    in public/world/environment.xml (both routed through the 'music' bus,
//    so the volume slider still controls them).
const BgmSystem = createMusicLayerDriver({
  resolve: () => (anyCreatureAggro() ? 'battle' : 'explore'),
  debounceMs: 1000,
});

// Must register quests before runtime.start() so the scene parser can resolve
// each <DialogueNPC dialogue-id> to its quest index. JSON import widens
// objective.type to `string`, so bridge to the literal union via double assert.
function loadQuests(raw: unknown): readonly QuestDef[] {
  const list = (Array.isArray(raw) ? raw : [raw]) as readonly unknown[];
  return list as unknown as readonly QuestDef[];
}

let bootstrapPromise: Promise<void> | null = null;

async function bootstrap(): Promise<void> {
  // One boot per page load — concurrent re-entry used to race resetBuilder()
  // against a live runtime and leave the tab stuck after Vite full-reload.
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = runBootstrap();
  return bootstrapPromise;
}

async function runBootstrap(): Promise<void> {
  // Clear any stale builder before registering plugins (never dispose a live
  // runtime mid-boot — that used to race with Vite full-reload teardown).
  resetBuilder();

  const bootLang = detectLocale();
  setLoadingScreenLocale(bootLang);
  mountLoadingScreen({
    title: 'Discordia',
    subtitle:
      bootLang === 'pt' ? 'Preparando o mundo…' : 'Preparing the world…',
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
  withPlugin(ProfilerPlugin);

  withSystem(PlayerSetupSystem);
  withSystem(PlayerStatsSystem);
  withSystem(RespawnSystem);
  withSystem(CombatFeedbackSystem);
  withSystem(AttackContextSystem);
  withSystem(BombSystem);
  withSystem(BombAimSpineSystem);
  withSystem(BgmSystem);
  withSystem(NotaSystem);

  configure({ canvas: '#game-canvas' });

  const runtime = await getBuilder().build();
  const state = runtime.getState();

  // Pack behavior: hitting one enemy alerts nearby allies with line of sight
  // to the player, so a pack fights together instead of each mob aggroing alone.
  setupAggroChain(state);

  GRIPS = await loadHeldItemGrips('/data/held-items.json');

  // City exclusion zone — registered directly in the occupancy registry before
  // any StaticSpawner samples positions. Central walled city is at the origin
  // (matches <SpawnExclusion at="0 0" radius="52"> in public/world/cities/discordia.xml).
  const villageZones: Array<[number, number, number]> = [[0, 0, 52]];
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

  // A Nota (GDD F1): quests de traçado passam a contar por [F] no marco, não
  // por proximidade — o gesto é o sistema-assinatura do jogo.
  initNota(state);

  // Estado da Nota: marcos anotados + biomas fixados. É estado de *mundo*, não
  // do jogador, mas viaja no mesmo save (contratos-de-dados.md §Estado de A Nota).
  registerSaveSerializer(state, 'simple-rpg-nota', {
    serialize: (s, eid) => {
      if (s.getEntityByName('player') !== eid) return null;
      return notaSnapshot();
    },
    deserialize: (s, eid, data) => {
      if (s.getEntityByName('player') !== eid) return;
      restoreNota(s, (data ?? {}) as Partial<NotaSnapshot>);
    },
  });

  // Persist merchant progress that lives outside ECS (playerStats.ringOwned /
  // swordLevel) so re-loading can't re-grant the ring (speed compounding) or
  // reset sword levels. Attached to the player entity; other eids are skipped.
  registerSaveSerializer(state, 'simple-rpg-progress', {
    serialize: (s, eid) => {
      if (s.getEntityByName('player') !== eid) return null;
      return {
        ringOwned: playerStats.ringOwned,
        swordLevel: playerStats.swordLevel,
      };
    },
    deserialize: (s, eid, data) => {
      if (s.getEntityByName('player') !== eid) return;
      const d = data as { ringOwned?: boolean; swordLevel?: number };
      playerStats.ringOwned = !!d.ringOwned;
      playerStats.swordLevel = d.swordLevel ?? 0;
    },
  });

  // Item definitions — without a registered ItemDef the inventory caps every
  // item's stack at 1, so bought bombs never accumulated. Stack them high.
  const itemReg = getDataRegistry(state);
  for (const [id, name, icon, description, tags] of [
    [
      'bomb',
      'Bomba',
      '/assets/icons/item_bomb.png',
      'Explosivo arremessável. Segure B para mirar, solte para lançar.',
      ['combat', 'consumable'],
    ],
    [
      'wood',
      'Madeira',
      '/assets/icons/hud_wood.png',
      'Madeira cortada das florestas do vale. Serve pra vender ou craft.',
      ['material'],
    ],
    [
      'stone',
      'Pedra',
      '/assets/icons/hud_stone.png',
      'Rocha minerada. Útil pra comércio e construção.',
      ['material'],
    ],
    [
      'potion',
      'Poção',
      '/assets/icons/potion_health.png',
      'Restaura vida. Atalho: 1.',
      ['consumable', 'heal'],
    ],
    [
      'antidote',
      'Antídoto',
      '/assets/icons/item_antidote.png',
      'Remove veneno. Atalho: 2.',
      ['consumable'],
    ],
    [
      'wolf_pelt',
      'Pele de lobo',
      '/assets/icons/wolf_pelt.png',
      'Pele grossa dos lobos da Floresta Sombria. Troféu de missão.',
      ['quest', 'loot'],
    ],
    [
      'cactus_fiber',
      'Fibra de cacto',
      '/assets/icons/cactus_fiber.png',
      'Fibra resistente do deserto. Usada nas rotas do leste.',
      ['quest', 'material'],
    ],
    [
      'silk_cloth',
      'Tecido de seda',
      '/assets/icons/silk_cloth.png',
      'Pano fino das caravanas do leste.',
      ['quest', 'material'],
    ],
    [
      'ancient_relic',
      'Relíquia antiga',
      '/assets/icons/ancient_relic.png',
      'Artefato gasto das ruínas do deserto.',
      ['quest', 'relic'],
    ],
    [
      'moss_potion',
      'Poção de musgo',
      '/assets/icons/moss_potion.png',
      'Bebida do pântano com gosto forte de erva.',
      ['quest', 'consumable'],
    ],
    [
      'iron_axe',
      'Machado de ferro',
      '/assets/icons/iron_axe.png',
      'Machado resistente forjado pra trabalho pesado.',
      ['quest', 'tool'],
    ],
    [
      'blessed_rod',
      'Vara abençoada',
      '/assets/icons/blessed_rod.png',
      'Cajado marcado com runas do vale.',
      ['quest', 'relic'],
    ],
    [
      'nature_amulet',
      'Amuleto da natureza',
      '/assets/icons/nature_amulet.png',
      'Amuleto tecido de cipós vivos.',
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
  registerDebugAction(state, 'give', (id: string, n: number = 1) => {
    const h = state.getEntityByName('player') ?? 0;
    if (h) addItem(state, h, id, n);
  });
  registerDebugAction(state, 'gold', (n: number = 100) => addGold(n));
  // Teleporte de QA (vite DEV only): move o body do herói para x y z.
  //   __VIBEGAME__.debug.callAction('tp', -410, 156, 118.5)
  registerDebugAction(state, 'tp', (x: number, y: number, z: number) => {
    const h = state.getEntityByName('player') ?? 0;
    if (!h) return -1;
    teleportEntity(state, h, x, y, z);
    return h;
  });
  // A Nota: __VIBEGAME__.debug.getVar('nota') → { marked, fixed, signed }
  registerDebugVar(state, 'nota', () => ({
    ...notaSnapshot(),
    progress: Object.fromEntries(
      BIOME_IDS.map((b) => [b, `${biomeProgress(b)}/3`])
    ),
  }));

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

  // i18n: shared modal/options keys come from examples/shared, game keys here.
  initI18n(state, { en: dictEN, pt: dictPT });

  // Harvest loot: the engine DestructiblePlugin breaks rocks/trees; the game
  // banks the yield into the player vault + bag and pops a floating "+1". The
  // loot joins the harvest stack (engine break popup + hit icon) so the three
  // texts line up instead of overlapping at the prop's origin.
  onDestructibleDestroyed(state, (eid, x, y, z) => {
    const player = state.getEntityByName('player');
    if (player === null) return;
    if (eid !== null && isWoodEntity(eid)) {
      addWood(1);
      addItem(state, player, 'wood', 1);
      notifyResourceHarvested(state, 'wood');
      const flavour = biomeHarvestKind('wood', x, z);
      if (flavour) notifyResourceHarvested(state, flavour);
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
      playSoundAt('chop-break', x, y, z, {
        originEid: eid ?? undefined,
      });
    } else {
      addStone(1);
      addItem(state, player, 'stone', 1);
      notifyResourceHarvested(state, 'stone');
      const flavour = biomeHarvestKind('stone', x, z);
      if (flavour) notifyResourceHarvested(state, flavour);
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
      playSoundAt('mine-break', x, y, z, {
        originEid: eid ?? undefined,
      });
    }
  });

  // Engine OptionsTab rows → audio buses + Save/Load buttons (shared wiring).
  wireOptions(state, {
    saveKey: SAVE_KEY,
    onSave: () => playSound('save'),
    onLoad: (restored) => {
      if (restored) playSound('load');
    },
  });

  initAudioBuses(state);

  // QA / debug surface (registered through the engine DebugPlugin overlay;
  // DEV-gated by the registry itself). Invoke via:
  //   __VIBEGAME__.debug.getVar('playerDebug')
  //   __VIBEGAME__.debug.callAction('spawnFloatingText', 'hi', 0, 2, 0)
  //   __VIBEGAME__.profiler.top(15)
  registerProfilerDebug(state);
  registerDebugVar(state, 'playerState', () => state);
  registerDebugVar(state, 'diagTerrainReady', () => terrainReady(state));
  registerDebugVar(state, 'diagDynamicsBlocking', () =>
    isTerrainDynamicsBlocking(state)
  );
  registerDebugVar(state, 'diagPlayerFeet', () => {
    const playerEid = state.getEntityByName('player');
    if (playerEid === null) return { err: 'no player' };
    const x = Transform.posX[playerEid];
    const z = Transform.posZ[playerEid];
    const terrainH = getTerrainHeightAt(state, x, z);
    const bvh = getBvhSurfaceHeight(state, x, 500, z);
    const body = getBodyForEntity(state, playerEid);
    return {
      x,
      z,
      posY: Transform.posY[playerEid],
      terrainH,
      bvh,
      bodyY: body?.translation().y ?? null,
    };
  });
  registerDebugVar(state, 'diagTerrainCtx', () => {
    const out: Record<string, unknown> = {};
    for (const [eid, data] of getTerrainContext(state)) {
      out[String(eid)] = {
        initialized: data.initialized,
        collisionReady: (data as unknown as Record<string, unknown>)
          .collisionReady,
        hasHeightmapUrl: !!(data as unknown as Record<string, unknown>)
          .heightmapUrl,
        samplerData: (() => {
          const sampler = (data as unknown as Record<string, unknown>)
            .sampler as { data?: unknown } | undefined;
          return !!sampler && !!sampler.data;
        })(),
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
  registerDebugVar(state, 'playerDebug', () => {
    const player = state.getEntityByName('player');
    if (player === null) return {};
    return {
      x: Transform.posX[player],
      y: Transform.posY[player],
      z: Transform.posZ[player],
      hp: Health.current[player] ?? 0,
      maxHp: Health.max[player] ?? 0,
      level: ProgressionComponent.level[player] ?? 0,
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
            (c: Object3D) => `${c.name}(s${c.scale.x.toFixed(2)})`
          ),
          worldScale: +_v.x.toFixed(3),
        });
      }
    });
    return JSON.stringify(hands);
  });

  // Audio unlock + deferred bank preload: Scene resume-audio-on-user-gesture
  // (engine resumeAudioContextOnFirstUserGesture → allowSoundPreload).

  await runtime.start();
}

void bootstrap();

// Soft HMR of this graph leaks WebGL/KTX2/Rapier in Firefox — decline so Vite
// always full-reloads. Unload path must stay lightweight: heavy destroy() here
// can hang mid-boot and block location.reload() (dead page after "Disposing").
setupHmrGuard(() => {
  clearBombs();
  clearAbilityBar();
  clearHotbar();
  clearMelee();
  clearNota();
  // GPU only — must not await Rapier/navmesh teardown before reload.
  releaseRuntimeGpuResources();
});
