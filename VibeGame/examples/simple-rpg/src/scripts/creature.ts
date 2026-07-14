import * as THREE from 'three';
import {
  loadGltfToSceneWithAnimator,
  notifyEnemyKilled,
  playSound,
  spawnFloatingText,
} from 'vibegame';
import type { GltfAnimator, MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  defineQuery,
  PlayerController,
  sampleTerrainHeight,
  getBvhSurfaceHeight,
  Health,
  isDead,
  spawnParticleBurst,
  // Engine melee-AI FSM (the brain): perception, state machine, navmesh
  // steering + attack. This script is the *presentation* layer on top of it.
  runMeleeAiFrame,
  getOrCreateAiInstanceState,
  removeAiInstanceState,
  AiStateComponent,
  AI_MODE_IDLE,
  AI_MODE_CHASE,
  AI_MODE_ATTACK,
  AI_MODE_LUNGE,
  AI_MODE_DEAD,
  removeAgent,
} from 'vibegame';
import type { MeleeAiConfig } from 'vibegame';
import {
  registerEnemy,
  setEnemyLabel,
  unregisterEnemy,
} from './enemy-registry';

const TERRAIN_LAYER = 0x0001;
const WATER_LEVEL = 1.25;

// AI tuning not expressed in CreatureConfig — defaults from the original
// creature prototype, fed into the engine MeleeAiConfig.
const AI_DEFAULTS = {
  detectRange: 18,
  // Attack from ~1m (matches the engine combat ring), not 2-3m.
  attackRange: 1.4,
  attackCooldown: 2.5,
  leashRadius: 30,
  lungeWindup: 0.25,
  lungeDuration: 0.3,
  lungeRecovery: 0.5,
  lungeStandoff: 0.9,
  hoverMin: 2.0,
  hoverMax: 5.0,
};

const aggroEntities = new Set<number>();
export function anyCreatureAggro(): boolean {
  return aggroEntities.size > 0;
}

export interface CreatureClips {
  idle: string;
  walk: string;
  run: string;
  lunge: string;
  death: string;
  /** Optional intro roar clip (boss). */
  roar?: string;
  /** Optional hit reaction clip (played when taking damage). */
  hit?: string;
  /** Optional attack clip (played during the attack swing between lunges). */
  attack?: string;
}

export interface CreatureConfig {
  modelUrl: string;
  clips: CreatureClips;
  hp: number;
  chaseSpeed: number;
  wanderSpeed: number;
  wanderRadius: number;
  attackDamage: number;
  lootGoldMin: number;
  lootGoldMax: number;
  onDeathLoot?: (
    state: State,
    gold: number,
    x: number,
    y: number,
    z: number
  ) => void;
  // ── Optional AI/boss extras (all default off) ──
  detectRange?: number;
  attackRange?: number;
  attackCooldown?: number;
  leashRadius?: number;
  /** Orbit/strafe the player between swings. */
  strafe?: boolean;
  /** Back off + circle below this HP fraction. */
  lowHpKiteFrac?: number;
  /** Enrage (faster, shorter cooldown) below this HP fraction. */
  enrageBelowFrac?: number;
  /** SFX played on the intro roar / first activation. */
  roarSound?: string;
  /** Big banner shown on death (boss). */
  defeatedText?: string;
  /** Stay dormant (hidden, no AI) until this returns true (boss gate). */
  gateUntil?: () => boolean;
  /** Enemy type identifier for quest kill tracking (e.g. 'wolf', 'shade'). */
  enemyType?: string;
  /** Time-scale applied to the run clip while chasing (e.g. 1.5 to reuse walk as a jog). */
  runTimeScale?: number;
  /**
   * Uniform visual scale applied to the loaded model (default 1).
   * The asset pipeline (Hunyuan) normalizes every GLB to a ~2-unit bounding
   * box — a mosquito ships as tall as the hero and the ogre *shorter* — so
   * each creature declares its real in-world size here. footOffset and the
   * health bar are children of the scaled group, so they follow for free.
   */
  modelScale?: number;
}

interface PresentationState {
  group: THREE.Group | null;
  animator: GltfAnimator | null;
  footOffset: number;
  ready: boolean;
  playing: string;
  heading: number;
  prevX: number;
  prevZ: number;
  lastHp: number;
  flashTimer: number;
  flashMats:
    { mat: THREE.MeshStandardMaterial; emHex: number; emInt: number }[] | null;
  deathHandled: boolean;
  deathTimer: number;
  /** Hit-reaction countdown: plays the hit clip, then returns to AI clip. */
  hitTimer: number;
  /** Gate: false while dormant (boss waiting), true once activated. */
  activated: boolean;
  /** Intro-roar countdown (holds still, plays roar clip). */
  roarTimer: number;
}

const playerQuery = defineQuery([PlayerController]);
const _box = new THREE.Box3();

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  // Static obstacles registered on TERRAIN_LAYER (rocks, props): a BVH raycast
  // lands the creature on top of them.
  const gy = getBvhSurfaceHeight(
    ctx.state,
    x,
    fromY + 60,
    z,
    2000,
    TERRAIN_LAYER
  );
  if (gy != null && Number.isFinite(gy)) return gy;
  // Terrain: anchor to the *rendered* LOD mesh surface across the footprint,
  // not the full-resolution analytic height. On coarse LOD chunks the flat mesh
  // triangles sit above the analytic surface in valleys, so an analytic anchor
  // leaves the creature sunk into the visible ground. sampleTerrainHeight
  // multi-samples the rendered lattice the hero's CCT collider stands on,
  // keeping the model flush with what is shown.
  const h = sampleTerrainHeight(ctx.state, x, z);
  return Number.isFinite(h) ? h : 0;
}

function collectFlashMats(s: PresentationState): void {
  if (s.flashMats || !s.group) return;
  const mats: {
    mat: THREE.MeshStandardMaterial;
    emHex: number;
    emInt: number;
  }[] = [];
  s.group.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    const arr = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const mat of arr) {
      const sm = mat as THREE.MeshStandardMaterial;
      if (sm && sm.emissive) {
        mats.push({
          mat: sm,
          emHex: sm.emissive.getHex(),
          emInt: sm.emissiveIntensity ?? 1,
        });
      }
    }
  });
  s.flashMats = mats;
}

function applyFlash(s: PresentationState, on: boolean): void {
  if (!s.flashMats) return;
  for (const f of s.flashMats) {
    if (on) {
      f.mat.emissive.setRGB(1, 1, 1);
      f.mat.emissiveIntensity = 1.4;
    } else {
      f.mat.emissive.setHex(f.emHex);
      f.mat.emissiveIntensity = f.emInt;
    }
  }
}

export interface CreatureBehaviours {
  start: (ctx: MonoBehaviourContext) => void;
  update: (ctx: MonoBehaviourContext) => void;
  onDestroy: (ctx: MonoBehaviourContext) => void;
}

export function createCreatureBehaviours(
  cfg: CreatureConfig
): CreatureBehaviours {
  // One shared FSM config per creature type. `targetEid` (the hero) is resolved
  // lazily — the engine FSM then chases/attacks it without needing a faction
  // hostility matrix set up.
  const meleeConfig: MeleeAiConfig = {
    detectRange: cfg.detectRange ?? AI_DEFAULTS.detectRange,
    attackRange: cfg.attackRange ?? AI_DEFAULTS.attackRange,
    attackCooldown: cfg.attackCooldown ?? AI_DEFAULTS.attackCooldown,
    attackDamage: cfg.attackDamage,
    chaseSpeed: cfg.chaseSpeed,
    wanderSpeed: cfg.wanderSpeed,
    wanderRadius: cfg.wanderRadius,
    leashRadius: cfg.leashRadius ?? AI_DEFAULTS.leashRadius,
    lungeWindup: AI_DEFAULTS.lungeWindup,
    lungeDuration: AI_DEFAULTS.lungeDuration,
    lungeRecovery: AI_DEFAULTS.lungeRecovery,
    lungeStandoff: AI_DEFAULTS.lungeStandoff,
    hoverMin: AI_DEFAULTS.hoverMin,
    hoverMax: AI_DEFAULTS.hoverMax,
    strafe: cfg.strafe,
    lowHpKiteFrac: cfg.lowHpKiteFrac,
    enrageBelowFrac: cfg.enrageBelowFrac,
  };

  const stateMap = new Map<number, PresentationState>();
  let cachedPlayer = 0;

  function resolvePlayer(ctx: MonoBehaviourContext): number {
    if (cachedPlayer && Health.current[cachedPlayer] > 0) return cachedPlayer;
    cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
    if (cachedPlayer) meleeConfig.targetEid = cachedPlayer;
    return cachedPlayer;
  }

  function handleDeath(
    ctx: MonoBehaviourContext,
    s: PresentationState,
    eid: number
  ): void {
    if (s.deathHandled) return;
    s.deathHandled = true;
    s.deathTimer = 2.0;
    aggroEntities.delete(eid);
    unregisterEnemy(eid);

    // Diagnostic: legit death has hp<=0; hp>0 means a stale DEAD mode slipped through.
    if (Health.current[eid] > 0) {
      console.warn(
        '[creature] spurious enemy-death: alive creature treated as dead',
        {
          eid,
          hp: Health.current[eid],
          mode: AiStateComponent.mode[eid],
          type: cfg.enemyType,
        }
      );
    }
    playSound('enemy-death');
    const x = Transform.posX[eid];
    const y = Transform.posY[eid];
    const z = Transform.posZ[eid];
    if (cfg.defeatedText) {
      spawnFloatingText(ctx.state, cfg.defeatedText, {
        x,
        y: y + 3.0,
        z,
        color: 0xffd700,
        size: 1.0,
        duration: 3.0,
      });
    }
    const gold = Math.floor(
      cfg.lootGoldMin + Math.random() * (cfg.lootGoldMax - cfg.lootGoldMin + 1)
    );
    cfg.onDeathLoot?.(ctx.state, gold, x, y, z);
    if (cfg.enemyType) {
      notifyEnemyKilled(ctx.state, cfg.enemyType);
    }
    playSound('item-drop');
    spawnParticleBurst(ctx.state, {
      x,
      y: y + 0.5,
      z,
      preset: 'explosion',
      count: 16,
      duration: 0.8,
    });
    if (s.animator && s.playing !== cfg.clips.death) {
      const acted = s.animator.play(cfg.clips.death, { loop: false });
      if (acted) s.playing = cfg.clips.death;
    }
  }

  function pickClip(mode: number, moving: boolean): string {
    // Only the actual lunge burst plays the lunge clip; while waiting between
    // swings (ATTACK) we play the attack clip if available (windup/recover),
    // otherwise idle so the rig doesn't freeze on the lunge's clamped last frame.
    if (mode === AI_MODE_LUNGE) return cfg.clips.lunge;
    if (mode === AI_MODE_CHASE) return cfg.clips.run;
    if (mode === AI_MODE_ATTACK) return cfg.clips.attack ?? cfg.clips.idle;
    return moving ? cfg.clips.walk : cfg.clips.idle;
  }

  /** Play clip; only stamp ``playing`` on success. Fall back to idle on miss
   * so flying packs (Hover/Soar, no Walk) never sticky-T-pose. */
  function playClip(
    s: PresentationState,
    clip: string,
    opts?: { loop?: boolean }
  ): boolean {
    if (!s.animator) return false;
    const acted = s.animator.play(clip, opts);
    if (acted) {
      s.playing = clip;
      return true;
    }
    if (clip !== cfg.clips.idle) {
      const idle = s.animator.play(cfg.clips.idle);
      if (idle) {
        s.playing = cfg.clips.idle;
      }
    }
    return false;
  }

  return {
    start(ctx: MonoBehaviourContext): void {
      const eid = ctx.entity;
      const s: PresentationState = {
        group: null,
        animator: null,
        footOffset: 0,
        ready: false,
        playing: '',
        heading: Math.random() * Math.PI * 2,
        prevX: Transform.posX[eid],
        prevZ: Transform.posZ[eid],
        lastHp: cfg.hp,
        flashTimer: 0,
        flashMats: null,
        deathHandled: false,
        deathTimer: 0,
        hitTimer: 0,
        activated: !cfg.gateUntil,
        roarTimer: 0,
      };
      stateMap.set(eid, s);

      if (!ctx.state.hasComponent(eid, Health))
        ctx.state.addComponent(eid, Health);
      Health.current[eid] = cfg.hp;
      Health.max[eid] = cfg.hp;
      if (cfg.enemyType) {
        const label =
          cfg.enemyType.charAt(0).toUpperCase() + cfg.enemyType.slice(1);
        setEnemyLabel(eid, label);
      }
      // AiStateComponent is a raw global array never cleared on eid recycle —
      // reset it so a fresh creature can't inherit a stale DEAD slot.
      AiStateComponent.mode[eid] = AI_MODE_IDLE;
      AiStateComponent.target[eid] = 0;

      // Normal enemies count toward the boss gate; the boss (gated) does not.
      if (!cfg.gateUntil)
        registerEnemy(eid, Transform.posX[eid], Transform.posZ[eid]);

      resolvePlayer(ctx);

      void loadGltfToSceneWithAnimator(ctx.state, cfg.modelUrl, {
        crossfadeDuration: 0.25,
      }).then((result) => {
        if (stateMap.get(eid) !== s) {
          result.group.removeFromParent();
          return;
        }
        s.group = result.group;
        s.animator = result.animator;
        const scale = cfg.modelScale ?? 1;
        if (scale !== 1) s.group.scale.setScalar(scale);
        s.group.updateWorldMatrix(true, true);
        _box.setFromObject(s.group);
        s.footOffset = Number.isFinite(_box.min.y) ? -_box.min.y : 0;
        if (!s.activated) s.group.visible = false; // dormant boss stays hidden
      });
    },

    update(ctx: MonoBehaviourContext): void {
      const eid = ctx.entity;
      const s = stateMap.get(eid);
      if (!s) return;

      resolvePlayer(ctx);

      // ── Boss gate: stay dormant (hidden, no AI) until the gate opens, then
      //    reveal + intro roar before engaging. ──────────────────────────────
      if (!s.activated) {
        if (cfg.gateUntil && !cfg.gateUntil()) return;
        s.activated = true;
        if (s.group) s.group.visible = true;
        if (cfg.clips.roar) {
          s.roarTimer = 2.5;
          if (cfg.roarSound) playSound(cfg.roarSound);
        }
      }
      if (s.roarTimer > 0 && s.group) {
        s.roarTimer -= ctx.deltaTime;
        s.animator?.update(ctx.deltaTime);
        if (cfg.clips.roar && s.playing !== cfg.clips.roar) {
          playClip(s, cfg.clips.roar, { loop: false });
        }
        const rx = Transform.posX[eid];
        const rz = Transform.posZ[eid];
        const ry = groundHeight(ctx, rx, rz, Transform.posY[eid]);
        if (Number.isFinite(ry)) {
          Transform.posY[eid] = ry + s.footOffset;
          s.group.position.set(rx, ry + s.footOffset, rz);
        }
        return;
      }

      // ── AI (engine FSM): perception, FSM, navmesh steering, attack damage.
      const inst = getOrCreateAiInstanceState(ctx.state, eid);
      runMeleeAiFrame(ctx.state, eid, meleeConfig, inst);

      // Presentation: visuals, clips, terrain-Y, hit-flash, death FX + loot.
      if (!s.group) return;
      s.animator?.update(ctx.deltaTime);
      const dt = ctx.deltaTime;
      const mode = AiStateComponent.mode[eid];

      if (mode === AI_MODE_DEAD || isDead(eid)) {
        handleDeath(ctx, s, eid);
        s.deathTimer -= dt;
        if (s.deathTimer <= 0) {
          s.group.removeFromParent();
          s.group = null;
        }
        return;
      }

      if (!s.ready) {
        const gy = groundHeight(
          ctx,
          Transform.posX[eid],
          Transform.posZ[eid],
          500
        );
        if (!Number.isFinite(gy)) return;
        s.ready = true;
      }

      // Hit flash + hit-reaction clip on HP drop (damage numbers/SFX come from main.ts watcher).
      if (s.flashTimer > 0) {
        s.flashTimer -= dt;
        if (s.flashTimer <= 0) applyFlash(s, false);
      }
      if (s.hitTimer > 0) s.hitTimer -= dt;
      const hp = Health.current[eid];
      if (s.lastHp > hp) {
        collectFlashMats(s);
        s.flashTimer = 0.11;
        applyFlash(s, true);
        // Play hit-reaction clip if available (brief stagger, then AI resumes).
        if (cfg.clips.hit && s.animator && mode !== AI_MODE_DEAD) {
          if (playClip(s, cfg.clips.hit, { loop: false })) {
            s.hitTimer = 0.35;
          }
        }
        spawnParticleBurst(ctx.state, {
          x: Transform.posX[eid],
          y: Transform.posY[eid] + 1.0,
          z: Transform.posZ[eid],
          preset: 'sparks',
          count: 6,
          duration: 0.4,
        });
      }
      s.lastHp = hp;

      // The FSM owns XZ (via the crowd agent / lunge). We own the terrain Y and
      // the visual transform.
      const x = Transform.posX[eid];
      const z = Transform.posZ[eid];
      const groundY = groundHeight(ctx, x, z, Transform.posY[eid]);
      const visualY =
        (Number.isFinite(groundY) ? groundY : Transform.posY[eid]) +
        s.footOffset;
      Transform.posY[eid] = visualY;
      Transform.dirty[eid] = 1;

      // Heading from planar movement; face the target while attacking.
      const vx = x - s.prevX;
      const vz = z - s.prevZ;
      const moveSpeed = dt > 0 ? Math.hypot(vx, vz) / dt : 0;
      if (moveSpeed > 0.3) {
        s.heading = Math.atan2(vx, vz);
      } else if (
        (mode === AI_MODE_ATTACK || mode === AI_MODE_LUNGE) &&
        cachedPlayer > 0
      ) {
        s.heading = Math.atan2(
          Transform.posX[cachedPlayer] - x,
          Transform.posZ[cachedPlayer] - z
        );
      }
      s.prevX = x;
      s.prevZ = z;

      s.group.position.set(x, visualY, z);
      s.group.rotation.set(0, s.heading, 0);

      const inCombat =
        mode === AI_MODE_CHASE ||
        mode === AI_MODE_ATTACK ||
        mode === AI_MODE_LUNGE;
      if (inCombat) aggroEntities.add(eid);
      else aggroEntities.delete(eid);

      // Clip selection: hit-reaction takes priority (brief stagger).
      // Then AI mode picks the locomotion/combat clip.
      let clip: string;
      if (s.hitTimer > 0 && cfg.clips.hit) {
        clip = cfg.clips.hit;
      } else {
        clip = pickClip(mode, moveSpeed > 0.3);
      }
      if (s.animator && s.playing !== clip) {
        playClip(
          s,
          clip,
          clip === cfg.clips.lunge || clip === cfg.clips.hit
            ? { loop: false }
            : undefined
        );
      }
      if (s.animator && cfg.runTimeScale !== undefined) {
        s.animator.setTimeScale(mode === AI_MODE_CHASE ? cfg.runTimeScale : 1);
      }
    },

    onDestroy(ctx: MonoBehaviourContext): void {
      const s = stateMap.get(ctx.entity);
      if (s) {
        s.group?.removeFromParent();
      }
      removeAgent(ctx.state, ctx.entity);
      removeAiInstanceState(ctx.state, ctx.entity);
      AiStateComponent.mode[ctx.entity] = AI_MODE_IDLE;
      AiStateComponent.target[ctx.entity] = 0;
      unregisterEnemy(ctx.entity);
      stateMap.delete(ctx.entity);
      aggroEntities.delete(ctx.entity);
    },
  };
}
