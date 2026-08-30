import * as THREE from 'three';
import {
  GltfAnimator,
  GltfPending,
  forEachLodChild,
  getGltfRootGroup,
  loadGltfMasterTracked,
  loadSettledGltfMaster,
  loadGltfToSceneWithAnimator,
  lodChildCount,
  notifyEnemyKilled,
  playSound,
  playSoundAt,
  spawnFloatingText,
  threeCameras,
} from 'aigamekit-vibegame';
import type { MonoBehaviourContext, State } from 'aigamekit-vibegame';
import {
  Transform,
  Parent,
  defineQuery,
  PlayerController,
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
  AI_LUNGE_PHASE_WINDUP,
  staggerAi,
  NavMeshAgent,
  removeAgent,
  planarYawRadians,
  setTransformYawRadians,
  shortestAngleDelta,
  markRigidbodyPoseDirty,
  spawnProjectileFromTemplate,
  hasLineOfSight,
  Rigidbody,
} from 'aigamekit-vibegame';
import type { MeleeAiConfig } from 'aigamekit-vibegame';
import {
  registerEnemy,
  setEnemyLabel,
  unregisterEnemy,
} from './enemy-registry';

/**
 * Sleep only when far from the player **and** distance-culled (mesh hidden).
 * Visible-on-camera packs keep AI/anim; near-player always wakes.
 *
 * Grounding: `<Creature>` CCT + heightfield. GLB must ship feet at origin
 * (pipeline `export_origin: feet`). No script Y lift / snap.
 */
const SLEEP_RANGE_MARGIN = 8;
const SLEEP_CHECK_INTERVAL = 20;

/**
 * Fraction of max HP a single blow must take to count as heavy — crits,
 * finishers and boss-tier weapons cross it, ordinary swings do not. Heavy hits
 * get the bigger reaction clip, the longer stagger and the fatter spark burst.
 */
const HEAVY_HIT_FRAC = 0.16;

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
/** Living boss eids — aggro de um deles deve sobrepor-se ao 'battle' normal. */
const bossEntities = new Set<number>();

/** True enquanto algum boss está vivo E em combate (camada BGM 'boss'). */
export function anyBossAggro(): boolean {
  for (const eid of aggroEntities) if (bossEntities.has(eid)) return true;
  return false;
}
export function anyCreatureAggro(): boolean {
  return aggroEntities.size > 0;
}

/**
 * Poise check for attacker-side effects (melee.ts): bosses don't get shoved
 * or staggered by ordinary blows — the sword stops on them.
 */
export function isBossCreature(eid: number): boolean {
  return bossEntities.has(eid);
}

/**
 * Presentation state must survive Vite dual-module identity splits: ``start``
 * may run in one copy of this file while ``update`` runs in another, each with
 * its own closure ``Map``. A ``globalThis`` WeakMap keyed by State keeps them
 * shared so goblins don't silently no-op (idle forever, leash stuck at 0).
 */
type PresentationStore = WeakMap<State, Map<number, PresentationState>>;
function presentationStore(): PresentationStore {
  const g = globalThis as typeof globalThis & {
    __vgCreaturePresentation?: PresentationStore;
  };
  if (!g.__vgCreaturePresentation) {
    g.__vgCreaturePresentation = new WeakMap();
  }
  return g.__vgCreaturePresentation;
}

function presentationMap(state: State): Map<number, PresentationState> {
  const store = presentationStore();
  let m = store.get(state);
  if (!m) {
    m = new Map();
    store.set(state, m);
  }
  return m;
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
  /**
   * Heavy-hit reaction. A crit or a big chunk of the health bar plays this
   * instead of `hit` — one reaction for every blow reads flat, and packs ship
   * a `knockback`/`hithead` clip that is exactly the "that one hurt" pose.
   */
  knockback?: string;
  /**
   * Telegraph clip played during the lunge windup (`roar`, `swordheavy`…).
   * The windup already ramps an emissive glow; a wind-up pose makes the same
   * beat readable from the silhouette instead of only from the tint.
   */
  windup?: string;
  /** Optional attack clip(s) played during the attack swing between lunges.
   * A string[] is a variety pool — successive swings cycle through it. */
  attack?: string | string[];
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
  /** Seconds the creature braces (telegraph) before a lunge burst. */
  lungeWindup?: number;
  /** Seconds the lunge burst travels. */
  lungeDuration?: number;
  /** Seconds the creature pauses (vulnerable) after a lunge. */
  lungeRecovery?: number;
  /** Min gap kept between creature and player during a lunge (anti-overlap). */
  lungeStandoff?: number;
  /** Enrage speed multiplier (default 1.4). */
  enrageSpeedMult?: number;
  /** Enrage cooldown multiplier (default 0.5). */
  enrageCooldownMult?: number;
  /** SFX played on the intro roar / first activation. */
  roarSound?: string;
  /** Big banner shown on death (boss). */
  defeatedText?: string;
  /** Stay dormant (hidden, no AI) until this returns true (boss gate). */
  gateUntil?: () => boolean;
  /** Enemy type identifier for quest kill tracking (e.g. 'wolf', 'shade'). */
  enemyType?: string;
  /** Boss flag: aggro dele alimenta a camada de BGM 'boss' (anyBossAggro). */
  isBoss?: boolean;
  /**
   * Seconds the creature's FSM freezes when it takes a hit (hit-stagger).
   * Default: 0.32 for regular mobs, 0 for bosses (poise). The shove itself
   * (knockback) is applied by the attacker — see melee.ts.
   */
  hitStaggerSec?: number;
  /** Time-scale applied to the run clip while chasing (e.g. 1.5 to reuse walk as a jog). */
  runTimeScale?: number;
  /**
   * Uniform visual scale applied to the loaded model (default 1).
   * The asset pipeline (Hunyuan) normalizes every GLB to a ~2-unit bounding
   * box — small pests ship as tall as the player and the ogre *shorter* — so
   * each creature declares its real in-world size here. Spawner AABB lift and
   * the health bar follow the scaled group.
   */
  modelScale?: number;
  /**
   * Prefer XML ``<GLTFLoader>`` visual from ``index.html`` (merged onto this
   * entity via ``merge: true``, or as a child). Default true when
   * ``modelUrl`` looks like a LOD asset (``_lodN.glb``).
   */
  visualFromIndex?: boolean;
  /**
   * Extra yaw (radians) when the GLB forward axis is not local +Z.
   * Quaternius / gameassets LOD packs face +Z — leave at 0. Use ``Math.PI``
   * only for assets that face −Z in bind pose.
   */
  facingYawOffset?: number;
  /**
   * When true (default), the creature only acquires the player when an
   * unobstructed line of sight exists (BVH raycast). Set false for sense-based
   * mobs that should aggro through walls.
   */
  requireLineOfSight?: boolean;
  /**
   * Optional steering/decision profile for the yuka AI layer. When set, the
   * creature additionally drives a {@link YukaAgentComponent} so it can pursuit
   * / evade / flock instead of the pure-melee chase ring. Omit to keep the
   * legacy melee-only behavior (back-compat).
   */
  behaviorProfile?: CreatureBehaviorProfile;
  /**
   * Registered projectile template id (see `<ProjectileTemplate>` in
   * `index.html`). When set, the creature becomes ranged: it holds a long
   * stand-off and fires this template on `rangedCooldown` seconds, and the
   * melee lunge is suppressed. The first creature type to use this becomes the
   * game's only ranged attacker.
   */
  rangedTemplate?: string;
  /** Seconds between ranged shots (default 2.0; only with `rangedTemplate`). */
  rangedCooldown?: number;
}

/**
 * Steering personality for the yuka layer. Maps directly to how a creature
 * *feels*: wolves back off after biting (hit-and-run), casters flee to range,
 * goblins dodge, tanks body-block. Optional fields default to the legacy
 * melee-chase ring when omitted.
 */
export interface CreatureBehaviorProfile {
  /** Below this HP fraction, flee toward maxRange instead of pressing in. */
  fleeBelowHpFrac?: number;
  /** Preferred stand-off distance from the player (m). 0 = body-block (legacy). */
  standOffRange?: number;
  /** Distance at which the creature stops fleeing and re-engages (m). */
  reengageRange?: number;
  /** Kite (evade while firing) when the player is close, vs pure flee. */
  kite?: boolean;
  /** Apply separation so the creature does not stack on allies. */
  separate?: boolean;
  /** Flock with allies (alignment + cohesion + separation). */
  flock?: boolean;
}

interface PresentationState {
  group: THREE.Group | null;
  animator: GltfAnimator | null;
  /** Per-LOD animators (index = lod level); kept in sync for seamless switches. */
  lodAnimators: (GltfAnimator | null)[];
  /**
   * Levels already attempted (attached, clip-less master, or failed fetch).
   * Without this a clip-less master would be re-requested every frame, which
   * keeps re-arming the boot `assets` gate and the loading screen never fades.
   */
  lodAttempted: boolean[];
  /** Visual owned by child GLTFLoader — do not scene-position the group. */
  xmlVisual: boolean;
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
  /** Hit-reaction countdown: plays the reaction clip, then returns to AI clip. */
  hitTimer: number;
  /** Which reaction clip the countdown is holding (`hit` or `knockback`). */
  hitClip: string;
  /** Gate: false while dormant (boss waiting), true once activated. */
  activated: boolean;
  /** Intro-roar countdown (holds still, plays roar clip). */
  roarTimer: number;
  /** Frames spent waiting for index.html GLTFLoader child. */
  xmlWaitFrames: number;
  /** Countdown to the next LOD-animator retry after a transient load fail. */
  lodRetryTimer: number;
  /** Seconds the creature has been outside the camera frustum (sleep grace). */
  outOfViewFor: number;
  /** Watchdog: last animator time seen (frozen-animation detection). */
  wdLastTime: number;
  /** Watchdog: consecutive checks where the animator did not advance. */
  wdStuck: number;
  /** Clips whose play already failed once (log de-dup). */
  failedClipWarns?: Set<string>;
  /** Per-LOD animator time for the watchdog (index aligned with lodAnimators). */
  wdLodTimes: number[];
  /** Transient retries left before giving up on LOD animators for good. */
  lodRetriesLeft: number;
  /** LOD levels already seen (parked included) — arms the late-attach scan. */
  lodSeenCount: number;
  /** True while beyond sleep range — AI/nav/anim paused. */
  sleeping: boolean;
  /** Seconds remaining before the next ranged shot (ranged creatures only). */
  rangedCdTimer: number;
  /** Seconds elapsed in the current lunge windup (telegraph glow ramp). */
  windupElapsed: number;
  /** Next index into the attack variety pool (advance per completed swing). */
  attackIdx: number;
  /** Last AI mode seen by pickClip — detects lunge→attack transitions. */
  lastPickMode: number;
}

const playerQuery = defineQuery([PlayerController]);
const cameraQuery = defineQuery([Transform]);
// ── Predictive camera-frustum wake ───────────────────────────────────────────
// Creatures used to wake by DISTANCE only (detect+8 m), so a mob you could
// clearly see stayed frozen until you walked close — very perceptible. Now the
// wake rule is the camera FRUSTUM with a margin: animate unless the creature
// is outside the (slightly expanded) view angle, and start BEFORE it enters
// the frame. The frustum is computed once per frame and shared by all mobs.
const FRUSTUM_WAKE_MARGIN = 10; // meters of predictive slack around each mob
const FRUSTUM_SLEEP_GRACE = 2.5; // seconds outside the frustum before sleeping
let _frustumFrame = -1;
let _frustumValid = false;
const _frustum = new THREE.Frustum();
const _projScreen = new THREE.Matrix4();
const _wakeSphere = new THREE.Sphere();

function cameraFrustum(state: State): THREE.Frustum | null {
  if (_frustumFrame === state.time.frameCount) {
    return _frustumValid ? _frustum : null;
  }
  _frustumFrame = state.time.frameCount;
  _frustumValid = false;
  // The engine keeps one THREE.Camera per MainCamera entity; use the first
  // live instance (the third-person follow camera).
  for (const cam of threeCameras.values()) {
    if (!cam.projectionMatrix) continue;
    _projScreen.multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
    _frustum.setFromProjectionMatrix(_projScreen);
    _frustumValid = true;
    break;
  }
  return _frustumValid ? _frustum : null;
}

/** True when the creature is inside (or near) the camera view angle. */
function withinCameraView(state: State, eid: number): boolean {
  const frustum = cameraFrustum(state);
  if (!frustum) return true; // no camera yet — never gate on it
  _wakeSphere.center.set(
    Transform.posX[eid],
    Transform.posY[eid] + 1,
    Transform.posZ[eid]
  );
  _wakeSphere.radius = FRUSTUM_WAKE_MARGIN;
  return frustum.intersectsSphere(_wakeSphere);
}
const xmlVisualQuery = defineQuery([Parent, GltfPending]);
/** Planar speed (m/s) above which chase/idle facing follows displacement. */
const MOVE_FACE_SPEED = 0.3;

function deriveLodUrls(modelUrl: string): [string, string, string] | null {
  const m = modelUrl.match(/^(.*)_lod([012])\.glb$/i);
  if (!m) return null;
  const base = m[1]!;
  const near = Number(m[2]);
  // Runtime stacks that already start mid/far must not pull denser masters
  // just to feed AnimationMixer clips.
  if (near >= 2) {
    return [`${base}_lod2.glb`, `${base}_lod2.glb`, `${base}_lod2.glb`];
  }
  if (near >= 1) {
    return [`${base}_lod1.glb`, `${base}_lod2.glb`, `${base}_lod2.glb`];
  }
  return [`${base}_lod0.glb`, `${base}_lod1.glb`, `${base}_lod2.glb`];
}

/** Sibling master URL for `level` (`goblin_lod2.glb` → `goblin_lod0.glb`). */
function lodUrlAtLevel(modelUrl: string, level: number): string | null {
  const m = modelUrl.match(/^(.*)_lod[012]\.glb$/i);
  if (!m || level < 0 || level > 2) return null;
  return `${m[1]}_lod${level}.glb`;
}

function findXmlVisualChild(state: State, parentEid: number): number | null {
  for (const child of xmlVisualQuery(state.world)) {
    if (Parent.entity[child] !== parentEid) continue;
    if (GltfPending.loaded[child] !== 1) continue;
    if (getGltfRootGroup(state, child)) return child;
  }
  return null;
}

/**
 * ``GLTFLoader`` uses ``merge: true``, so the visual usually lives on the
 * GameObject itself — not as a Parent-linked child. Fall back to legacy
 * child lookup for older layouts.
 */
function resolveXmlVisualEid(state: State, eid: number): number | null {
  if (
    state.hasComponent(eid, GltfPending) &&
    GltfPending.loaded[eid] === 1 &&
    getGltfRootGroup(state, eid)
  ) {
    return eid;
  }
  return findXmlVisualChild(state, eid);
}

function isXmlVisualPending(state: State, eid: number): boolean {
  if (state.hasComponent(eid, GltfPending) && GltfPending.loaded[eid] !== 1) {
    return true;
  }
  for (const child of xmlVisualQuery(state.world)) {
    if (Parent.entity[child] !== eid) continue;
    if (GltfPending.loaded[child] !== 1) return true;
  }
  return false;
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

/**
 * Telegraph glow while the creature braces for a lunge: amber → red, intensity
 * ramping with windup progress (`k` in 0..1). Gives the player a readable
 * dodge window without a bespoke windup animation clip. The white hit-flash
 * takes precedence (see the update loop).
 */
function applyWindupGlow(s: PresentationState, k: number): void {
  if (!s.flashMats) return;
  const g = 0.55 * (1 - k * 0.8);
  const b = 0.15 * (1 - k);
  for (const f of s.flashMats) {
    f.mat.emissive.setRGB(1, g, b);
    f.mat.emissiveIntensity = 0.5 + 1.1 * k;
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
  // A ranged creature holds a long stand-off and fires projectiles; the FSM is
  // still used for perception + positioning, but the lunge is suppressed (huge
  // cooldown) and the actual damage is dealt by `spawnProjectileFromTemplate`
  // in the update loop. This keeps all the presentation/sleep/loot machinery.
  const isRanged = !!cfg.rangedTemplate;
  // One shared FSM config per creature type. `targetEid` (the player) is resolved
  // lazily — the engine FSM then chases/attacks it without needing a faction
  // hostility matrix set up.
  const meleeConfig: MeleeAiConfig = {
    detectRange: cfg.detectRange ?? AI_DEFAULTS.detectRange,
    // Ranged: engage at stand-off distance so ATTACK mode kicks in early and the
    // creature holds its firing ring instead of closing to melee.
    attackRange: cfg.attackRange ?? (isRanged ? 9 : AI_DEFAULTS.attackRange),
    // Suppress the lunge for ranged attackers (cooldown ~never). The update
    // loop owns the fire cadence via `rangedCooldown`.
    attackCooldown:
      cfg.attackCooldown ?? (isRanged ? 9999 : AI_DEFAULTS.attackCooldown),
    attackDamage: cfg.attackDamage,
    chaseSpeed: cfg.chaseSpeed,
    wanderSpeed: cfg.wanderSpeed,
    wanderRadius: cfg.wanderRadius,
    leashRadius: cfg.leashRadius ?? AI_DEFAULTS.leashRadius,
    lungeWindup: cfg.lungeWindup ?? AI_DEFAULTS.lungeWindup,
    lungeDuration: cfg.lungeDuration ?? AI_DEFAULTS.lungeDuration,
    lungeRecovery: cfg.lungeRecovery ?? AI_DEFAULTS.lungeRecovery,
    lungeStandoff: cfg.lungeStandoff ?? AI_DEFAULTS.lungeStandoff,
    hoverMin: AI_DEFAULTS.hoverMin,
    hoverMax: AI_DEFAULTS.hoverMax,
    strafe: cfg.strafe,
    lowHpKiteFrac: cfg.lowHpKiteFrac,
    enrageBelowFrac: cfg.enrageBelowFrac,
    enrageSpeedMult: cfg.enrageSpeedMult,
    enrageCooldownMult: cfg.enrageCooldownMult,
    // Line-of-sight on by default: creatures must actually see the player before
    // aggroing, instead of beelining through walls. Individual wrappers can
    // opt out (cfg.requireLineOfSight === false) for blind/sense-based mobs.
    requireLineOfSight: cfg.requireLineOfSight ?? true,
  };

  let cachedPlayer = 0;
  const sleepRange =
    (meleeConfig.detectRange ?? AI_DEFAULTS.detectRange) + SLEEP_RANGE_MARGIN;
  const sleepRangeSq = sleepRange * sleepRange;

  function resolvePlayer(ctx: MonoBehaviourContext): number {
    if (cachedPlayer && Health.current[cachedPlayer] > 0) return cachedPlayer;
    cachedPlayer = playerQuery(ctx.state.world)[0] ?? 0;
    if (cachedPlayer) meleeConfig.targetEid = cachedPlayer;
    return cachedPlayer;
  }

  function setNavEnabled(state: State, eid: number, enabled: boolean): void {
    if (!state.hasComponent(eid, NavMeshAgent)) return;
    NavMeshAgent.enabled[eid] = enabled ? 1 : 0;
    if (!enabled) {
      // Drop path so the crowd doesn't keep simulating a sleeper.
      removeAgent(state, eid);
    }
  }

  /** True when the creature should full-simulate this frame. */
  function shouldSimulate(
    ctx: MonoBehaviourContext,
    s: PresentationState,
    eid: number
  ): boolean {
    const frame = ctx.state.time.frameCount;

    // Gated bosses only need a cheap gate poll until reveal.
    if (!s.activated) {
      return (frame + eid) % SLEEP_CHECK_INTERVAL === 0;
    }

    const mode = AiStateComponent.mode[eid];
    if (
      mode === AI_MODE_CHASE ||
      mode === AI_MODE_ATTACK ||
      mode === AI_MODE_LUNGE ||
      mode === AI_MODE_DEAD ||
      isDead(eid) ||
      s.deathHandled ||
      s.roarTimer > 0 ||
      s.hitTimer > 0
    ) {
      if (s.sleeping) {
        s.sleeping = false;
        setNavEnabled(ctx.state, eid, true);
      }
      return true;
    }

    // Near player → always awake (even if somehow culled).
    const player = resolvePlayer(ctx);
    if (!player) return true;

    const dx = Transform.posX[eid] - Transform.posX[player];
    const dz = Transform.posZ[eid] - Transform.posZ[player];
    const distSq = dx * dx + dz * dz;
    if (distSq <= sleepRangeSq) {
      if (s.sleeping) {
        s.sleeping = false;
        setNavEnabled(ctx.state, eid, true);
        s.playing = '';
      }
      return true;
    }

    // In (or near) the camera view angle → keep simulating, with a short
    // grace after leaving the frame so a quick camera swing doesn't park a
    // visible mob mid-stride. This replaces the old DistanceCull gate, which
    // kept clearly visible distant mobs frozen until the player got close.
    const inView = withinCameraView(ctx.state, eid);
    s.outOfViewFor = inView ? 0 : s.outOfViewFor + (ctx.deltaTime || 0);
    if (inView || s.outOfViewFor < FRUSTUM_SLEEP_GRACE) {
      if (s.sleeping) {
        s.sleeping = false;
        setNavEnabled(ctx.state, eid, true);
        s.playing = '';
      }
      return true;
    }

    if (!s.sleeping) {
      s.sleeping = true;
      setNavEnabled(ctx.state, eid, false);
      aggroEntities.delete(eid);
      s.playing = '';
    }
    return false;
  }

  function handleDeath(
    ctx: MonoBehaviourContext,
    s: PresentationState,
    eid: number
  ): void {
    if (s.deathHandled) return;
    s.deathHandled = true;
    s.deathTimer = 1.6;
    aggroEntities.delete(eid);
    bossEntities.delete(eid);
    unregisterEnemy(eid);
    // Kill hit-flash so corpses don't sit white/glowing until despawn.
    if (s.flashMats) applyFlash(s, false);
    s.flashTimer = 0;
    // Never re-pull XML/fallback visuals after death (was resurrecting the mesh).
    s.xmlWaitFrames = 999;

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
    const x = Transform.posX[eid];
    const y = Transform.posY[eid];
    const z = Transform.posZ[eid];
    playSoundAt('enemy-death', x, y, z, {
      originEid: eid,
      pitch: 1 + (Math.random() * 2 - 1) * 0.08,
    });
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
    playSoundAt('item-drop', x, y, z, { originEid: eid });
    spawnParticleBurst(ctx.state, {
      x,
      y: y + 0.5,
      z,
      preset: 'explosion',
      count: 16,
      duration: 0.8,
    });
    if (s.playing !== cfg.clips.death) {
      if (playClip(s, cfg.clips.death, { loop: false })) {
        // Packs ship one death clip; a ±12% rate spread keeps a wiped pack
        // from collapsing in perfect unison.
        jitterTimeScale(s, 0.12);
      }
      // A corpse must not keep recoiling from the blow that killed it.
      for (const anim of animatorsOf(s)) anim.clearFlinch();
    }
  }

  function pickClip(
    s: PresentationState,
    mode: number,
    moving: boolean,
    winding: boolean
  ): string {
    // Only the actual lunge burst plays the lunge clip; while waiting between
    // swings (ATTACK) we play the attack clip if available (windup/recover),
    // otherwise idle so the rig doesn't freeze on the lunge's clamped last frame.
    if (mode === AI_MODE_LUNGE) {
      // Telegraph: the brace before the burst gets its own pose (roar, raised
      // axe…) so the tell is readable from the silhouette, not only from the
      // windup glow. Falls through to the lunge clip once the burst starts.
      if (winding && cfg.clips.windup) return cfg.clips.windup;
      return cfg.clips.lunge;
    }
    if (mode === AI_MODE_CHASE) return cfg.clips.run;
    if (mode === AI_MODE_ATTACK) {
      // Ranged units sit in ATTACK at long stand-off; looping the melee
      // `attack` clip looks like close swings that never deal damage (lunge
      // suppressed). Attack anim plays only when a projectile actually fires.
      if (isRanged) return moving ? cfg.clips.run : cfg.clips.idle;
      const pool = cfg.clips.attack;
      if (pool == null) return cfg.clips.idle;
      // Variety pool: advance once per completed swing (lunge→attack edge).
      if (
        s.lastPickMode === AI_MODE_LUNGE &&
        Array.isArray(pool) &&
        pool.length > 1
      ) {
        s.attackIdx = (s.attackIdx + 1) % pool.length;
      }
      return Array.isArray(pool)
        ? (pool[s.attackIdx % pool.length] as string)
        : pool;
    }
    return moving ? cfg.clips.walk : cfg.clips.idle;
  }

  /** Every live animator for this creature (all LOD levels stay in sync so a
   *  LOD switch mid-swing does not restart the pose). */
  function animatorsOf(s: PresentationState): GltfAnimator[] {
    const targets = s.lodAnimators.filter(
      (a): a is GltfAnimator => !!a && a.clipNames.length > 0
    );
    if (
      s.animator &&
      s.animator.clipNames.length > 0 &&
      !targets.includes(s.animator)
    ) {
      targets.push(s.animator);
    }
    return targets;
  }

  /** Nudge playback rate by ±`amount` — repeated reactions/swings played at
   *  exactly the same rate are what makes a mob read as a machine. */
  function jitterTimeScale(s: PresentationState, amount: number): void {
    const scale = 1 + (Math.random() * 2 - 1) * amount;
    for (const anim of animatorsOf(s)) anim.setTimeScale(scale);
  }

  /** Play clip; only stamp ``playing`` on success. Fall back to idle on miss
   * so flying packs (Hover/Soar, no Walk) never sticky-T-pose. */
  function playClip(
    s: PresentationState,
    clip: string,
    opts?: { loop?: boolean }
  ): boolean {
    const targets = animatorsOf(s);
    if (targets.length === 0) return false;
    let acted = false;
    for (const anim of targets) {
      if (anim.play(clip, opts)) acted = true;
    }
    if (acted) {
      s.playing = clip;
      return true;
    }
    if (clip !== cfg.clips.idle) {
      let idle = false;
      for (const anim of targets) {
        if (anim.play(cfg.clips.idle)) idle = true;
      }
      if (idle) s.playing = cfg.clips.idle;
    }
    if (!s.failedClipWarns) s.failedClipWarns = new Set();
    if (!s.failedClipWarns.has(clip)) {
      s.failedClipWarns.add(clip);
      console.warn(
        `[creature] clip play failed everywhere → idle fallback: clip=${clip}, type=${cfg.enemyType ?? '?'}, available=${targets[0]?.clipNames.slice(0, 12).join(',') ?? '(none)'}`
      );
    }
    return false;
  }

  function applyHeadingToTransform(
    state: State,
    eid: number,
    headingRad: number
  ): void {
    // Transform.eulerY is degrees; setTransformYawRadians keeps quat in sync
    // for TransformHierarchySystem / GltfSceneSync (WorldTransform path).
    setTransformYawRadians(Transform, eid, headingRad);
    // Physics owns Transform after each fixed step (`copyRigidbodyToTransforms`).
    // Player movement writes yaw to Rigidbody; creatures must do the same or
    // facing snaps back every physics tick → mesh blinks while walking.
    if (!state.hasComponent(eid, Rigidbody)) return;
    Rigidbody.eulerX[eid] = Transform.eulerX[eid];
    Rigidbody.eulerY[eid] = Transform.eulerY[eid];
    Rigidbody.eulerZ[eid] = Transform.eulerZ[eid];
    Rigidbody.rotX[eid] = Transform.rotX[eid];
    Rigidbody.rotY[eid] = Transform.rotY[eid];
    Rigidbody.rotZ[eid] = Transform.rotZ[eid];
    Rigidbody.rotW[eid] = Transform.rotW[eid];
    Rigidbody.poseDirty[eid] = 1;
  }

  function claimFacingOwnership(state: State, eid: number): void {
    // Single writer: presentation owns yaw; navmesh still drives XZ.
    if (state.hasComponent(eid, NavMeshAgent)) {
      NavMeshAgent.faceVelocity[eid] = 0;
    }
  }

  function bindGroup(
    s: PresentationState,
    group: THREE.Group,
    xmlVisual: boolean
  ): void {
    s.group = group;
    s.xmlVisual = xmlVisual;
    const scale = cfg.modelScale ?? 1;
    if (scale !== 1) group.scale.setScalar(scale);
    if (!s.activated) group.visible = false;
  }

  async function attachLodAnimators(
    state: State,
    s: PresentationState,
    urls: [string, string, string],
    eid: number
  ): Promise<void> {
    if (!s.group) return;
    const attach = (child: THREE.Object3D): void => {
      void attachLodAnimatorFor(state, s, urls, eid, child);
    };
    // Inactive LOD levels are parked off-graph (gltf-lod-parking): scanning
    // only attached children misses every level but the active one, and the
    // distance band then switches to an animator-less level — the creature
    // chases frozen in bind pose while its AI stays healthy. Parked levels
    // must get animators too; a mixer drives a detached subtree just fine.
    if (lodChildCount(s.group) === 0) {
      for (const child of s.group.children) attach(child);
    } else {
      forEachLodChild(s.group, attach);
    }
  }

  async function attachLodAnimatorFor(
    state: State,
    s: PresentationState,
    urls: [string, string, string],
    eid: number,
    child: THREE.Object3D
  ): Promise<void> {
    const level = (child.userData.lodLevel as number | undefined) ?? 0;
    if (s.lodAnimators[level] || s.lodAttempted[level]) return;
    const url = urls[level];
    if (!url) return;
    s.lodAttempted[level] = true;
    try {
      // Prefer a master that already settled this session (the LOD0 visual is
      // cloned from one, so it is always there): clips are identical across
      // levels, and waiting on a colder master leaves the first encounter
      // chasing in bind pose until the far LOD streams in. Always background:
      // the XML visual already holds the boot gate for this mesh; the
      // animator is presentation polish on top of it.
      const ownLevelUrl = lodUrlAtLevel(cfg.modelUrl, level);
      const settled =
        loadSettledGltfMaster(url) ??
        (ownLevelUrl ? loadSettledGltfMaster(ownLevelUrl) : null);
      const master = await (settled ??
        loadGltfMasterTracked(state, url, 'background'));
      if (!s.group) return;
      // A retry may have re-armed this level while the master was in flight —
      // the first continuation to land owns the animator, the other exits.
      if (s.lodAnimators[level]) return;
      if (!master.animations?.length) {
        // Master without clips (rigged-only / bad handoff) — skip so we
        // never spam "Clip idle not found. Available:". Log once: a mesh-only
        // LOD means that distance band renders in bind pose (frozen look).
        console.warn(
          `[creature] LOD master without animations: ${url} (eid ${eid ?? '?'}, type ${cfg.enemyType ?? '?'})`
        );
        return;
      }
      const anim = new GltfAnimator(master, {
        root: child,
        crossfadeDuration: 0.25,
      });
      s.lodAnimators[level] = anim;
      if (level === 0 || !s.animator) s.animator = anim;
      if (s.playing) anim.play(s.playing);
    } catch {
      // Transient fetch failure (cold-boot dev-server races leave GLB
      // requests failing while Vite is still compiling). A permanent skip
      // here left the creature without ANY animator — it chased the player
      // frozen solid ("não descongela"). Mark retryable; the update loop
      // re-attaches after a cooldown (capped by lodRetriesLeft). A real 404
      // burns through the retries quickly and then stays skipped.
      if (s.lodRetriesLeft > 0) {
        s.lodRetriesLeft -= 1;
        s.lodRetryTimer = 3;
        s.lodAttempted[level] = false;
      } else if (level === 0) {
        console.warn(
          `[creature] LOD animator retries exhausted (lod0) — creature will stay un-animated: eid ${eid ?? '?'}, type ${cfg.enemyType ?? '?'}, url ${url}`
        );
      }
    }
  }

  function loadFallbackSingle(
    ctx: MonoBehaviourContext,
    eid: number,
    s: PresentationState
  ): void {
    void loadGltfToSceneWithAnimator(ctx.state, cfg.modelUrl, {
      crossfadeDuration: 0.25,
    }).then((result) => {
      if (presentationMap(ctx.state).get(eid) !== s || s.group) {
        result.group.removeFromParent();
        return;
      }
      bindGroup(s, result.group, false);
      s.animator = result.animator;
      if (result.animator) s.lodAnimators[0] = result.animator;
    });
  }

  function tryAdoptXmlVisual(
    ctx: MonoBehaviourContext,
    eid: number,
    s: PresentationState
  ): boolean {
    const visualEid = resolveXmlVisualEid(ctx.state, eid);
    if (visualEid == null) return false;
    const group = getGltfRootGroup(ctx.state, visualEid);
    if (!group) return false;
    bindGroup(s, group, true);
    const urls = deriveLodUrls(cfg.modelUrl);
    if (urls) void attachLodAnimators(ctx.state, s, urls, eid);
    return true;
  }

  function start(ctx: MonoBehaviourContext): void {
    const eid = ctx.entity;
    if (cfg.isBoss) bossEntities.add(eid);
    const preferXml =
      cfg.visualFromIndex ??
      (/_lod[0-2]\.glb$/i.test(cfg.modelUrl) ||
        ctx.state.hasComponent(eid, GltfPending));
    const existing = presentationMap(ctx.state).get(eid);
    if (existing) {
      // Already initialized (module-split recovery calling start again).
      return;
    }
    const s: PresentationState = {
      group: null,
      animator: null,
      lodAnimators: [null, null, null],
      lodAttempted: [false, false, false],
      xmlVisual: false,
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
      xmlWaitFrames: preferXml ? 0 : 999,
      sleeping: false,
      rangedCdTimer: 0,
      windupElapsed: 0,
      attackIdx: 0,
      hitClip: '',
      lastPickMode: -1,
      lodRetryTimer: 0,
      lodRetriesLeft: 6,
      lodSeenCount: 0,
      outOfViewFor: 0,
      wdLastTime: -1,
      wdStuck: 0,
      wdLodTimes: [],
    };
    presentationMap(ctx.state).set(eid, s);

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
    // reset it so a fresh creature can't inherit a stale DEAD slot. Also
    // attach the component so peer-separation queries and the debug bridge
    // see the entity (writing SoA alone does not enroll it in bitECS).
    if (!ctx.state.hasComponent(eid, AiStateComponent)) {
      ctx.state.addComponent(eid, AiStateComponent);
    }
    AiStateComponent.mode[eid] = AI_MODE_IDLE;
    AiStateComponent.target[eid] = 0;
    AiStateComponent.cooldown[eid] = 0;
    claimFacingOwnership(ctx.state, eid);

    // Normal enemies count toward the boss gate; the boss (gated) does not.
    if (!cfg.gateUntil)
      registerEnemy(eid, Transform.posX[eid], Transform.posZ[eid]);

    resolvePlayer(ctx);

    if (!preferXml) {
      loadFallbackSingle(ctx, eid, s);
    } else {
      tryAdoptXmlVisual(ctx, eid, s);
    }
    // XML visual: resolved in update while waiting for merge/child GLTFLoader.
  }

  function update(ctx: MonoBehaviourContext): void {
    const eid = ctx.entity;
    const map = presentationMap(ctx.state);
    let s = map.get(eid);
    if (!s) {
      // Module-identity split: ``start`` ran in another copy of this file.
      // Re-run start so AI/ground/clips resume instead of silently no-op'ing.
      start(ctx);
      s = map.get(eid);
      if (!s) return;
    }

    // Adopt merged/self or child <GLTFLoader lod*> from index.html.
    // Skip once dead — re-adopt was bringing corpses back (glowing/fallen "sleep").
    if (!s.deathHandled && !s.group && s.xmlWaitFrames < 999) {
      if (tryAdoptXmlVisual(ctx, eid, s)) {
        // adopted
      } else if (isXmlVisualPending(ctx.state, eid)) {
        // Keep waiting while the XML loader is in flight — never fall back
        // to a duplicate lod0 scene.add() mesh.
        s.xmlWaitFrames = Math.min(s.xmlWaitFrames + 1, 179);
      } else if (s.xmlWaitFrames < 300) {
        s.xmlWaitFrames += 1;
      } else {
        s.xmlWaitFrames = 999;
        loadFallbackSingle(ctx, eid, s);
      }
    }

    // Retry transient LOD-animator failures: without this a creature whose
    // animator fetch failed during boot NEVER animates (frozen chaser).
    if (s.group && !s.animator && s.lodRetryTimer > 0 && !s.deathHandled) {
      s.lodRetryTimer -= ctx.deltaTime;
      if (s.lodRetryTimer <= 0) {
        for (let i = 0; i < s.lodAttempted.length; i++) {
          if (!s.lodAnimators[i]) s.lodAttempted[i] = false;
        }
        const urls = deriveLodUrls(cfg.modelUrl);
        if (urls) void attachLodAnimators(ctx.state, s, urls, eid);
      }
    }

    // Late-arriving lod1/lod2 children (streamed after near LOD). Attach
    // even while sleeping so a denser LOD doesn't appear in bind/jump pose.
    // lodChildCount includes parked levels — root.children.length is capped
    // at 1 by the LOD parking, so it can never reveal a late arrival.
    if (s.group && s.xmlVisual) {
      const urls = deriveLodUrls(cfg.modelUrl);
      if (urls) {
        const count = lodChildCount(s.group);
        if (count > s.lodSeenCount) {
          s.lodSeenCount = count;
          void attachLodAnimators(ctx.state, s, urls, eid);
        }
      }
    }

    // ── Boss gate: stay dormant (hidden, no AI) until the gate opens, then
    //    reveal + intro roar before engaging. ──────────────────────────────
    if (!s.activated) {
      // Cheap staggered poll — don't burn frames while waiting for the gate.
      if ((ctx.state.time.frameCount + eid) % SLEEP_CHECK_INTERVAL !== 0) {
        return;
      }
      if (cfg.gateUntil && !cfg.gateUntil()) return;
      s.activated = true;
      if (s.group) s.group.visible = true;
      if (cfg.clips.roar) {
        s.roarTimer = 2.5;
        if (cfg.roarSound) {
          playSoundAt(
            cfg.roarSound,
            Transform.posX[eid],
            Transform.posY[eid],
            Transform.posZ[eid],
            { originEid: eid }
          );
        }
      }
    } else if (!shouldSimulate(ctx, s, eid)) {
      // Sleeping: park every animator on idle frame 0 so frozen chase/lunge
      // poses — and far-LOD animators that attach while asleep — never sit
      // in bind pose.
      if (s.group && s.playing !== cfg.clips.idle) playClip(s, cfg.clips.idle);
      for (const anim of s.lodAnimators) anim?.poseFrozenFrame();
      return;
    }

    // Ensure the FSM always has the player as explicit target while awake.
    resolvePlayer(ctx);

    if (s.roarTimer > 0 && s.group) {
      s.roarTimer -= ctx.deltaTime;
      for (const anim of s.lodAnimators) anim?.update(ctx.deltaTime);
      if (cfg.clips.roar && s.playing !== cfg.clips.roar) {
        playClip(s, cfg.clips.roar, { loop: false });
      }
      if (!s.xmlVisual) {
        s.group.position.set(
          Transform.posX[eid],
          Transform.posY[eid],
          Transform.posZ[eid]
        );
      }
      return;
    }

    // ── AI (engine FSM): perception, FSM, navmesh steering, attack damage.
    // Ranged: hold a long stand-off for arrows, but when the player closes to
    // melee distance re-enable the lunge so they aren't stuck playing fake
    // swings with cooldown ≈ ∞ (bandit-mesh archer bug).
    if (isRanged && cachedPlayer > 0) {
      const rdx = Transform.posX[cachedPlayer] - Transform.posX[eid];
      const rdz = Transform.posZ[cachedPlayer] - Transform.posZ[eid];
      const rdist = Math.hypot(rdx, rdz);
      if (rdist <= AI_DEFAULTS.attackRange) {
        meleeConfig.attackRange = AI_DEFAULTS.attackRange;
        meleeConfig.attackCooldown =
          cfg.attackCooldown ?? AI_DEFAULTS.attackCooldown;
      } else {
        meleeConfig.attackRange = cfg.attackRange ?? 9;
        meleeConfig.attackCooldown = 9999;
      }
    }
    const inst = getOrCreateAiInstanceState(ctx.state, eid);
    runMeleeAiFrame(ctx.state, eid, meleeConfig, inst);
    // Agent may be attached on first AI tick — reclaim yaw ownership.
    claimFacingOwnership(ctx.state, eid);

    // Presentation: visuals, clips, hit-flash, death FX + loot.
    if (!s.group) return;
    for (const anim of s.lodAnimators) anim?.update(ctx.deltaTime);

    // ── Frozen-animation watchdog ─────────────────────────────────────────
    // While awake, a looping locomotion clip must ALWAYS advance — on EVERY
    // LOD animator (the visible mesh may belong to a different level than
    // the primary). If a level stalls for ~2 s we log the full context once.
    let anyStuck = false;
    for (let li = 0; li < s.lodAnimators.length; li++) {
      const anim = s.lodAnimators[li];
      if (!anim || anim.clipNames.length === 0) continue;
      const t = anim.currentTime;
      const prev = s.wdLodTimes[li];
      if (prev !== undefined && Math.abs(t - prev) < 1e-6) {
        anyStuck = true;
      }
      s.wdLodTimes[li] = t;
    }
    if (anyStuck) {
      s.wdStuck++;
      if (s.wdStuck === 120) {
        // ~2 s at 60fps — only log the FIRST stall window per creature.
        const times = s.lodAnimators
          .map((a, i) =>
            a && a.clipNames.length > 0
              ? `lod${i}:${a.currentTime.toFixed(2)}`
              : `lod${i}:-`
          )
          .join(' ');
        console.warn(
          `[creature] FROZEN-ANIM watchdog: eid ${eid}, type ${cfg.enemyType ?? '?'}, mode ${AiStateComponent.mode[eid]}, playing=${s.playing}, [${times}], animators=${s.lodAnimators.filter(Boolean).length}, xmlVisual=${s.xmlVisual}, outOfView=${s.outOfViewFor.toFixed(1)}s`
        );
      }
    } else {
      s.wdStuck = 0;
    }
    if (s.animator && !s.lodAnimators.includes(s.animator)) {
      s.animator.update(ctx.deltaTime);
    }
    const dt = ctx.deltaTime;
    const mode = AiStateComponent.mode[eid];
    const inCombat =
      mode === AI_MODE_CHASE ||
      mode === AI_MODE_ATTACK ||
      mode === AI_MODE_LUNGE;

    // Ranged attack (casters/archers): fire a projectile on a cooldown when
    // engaged and the player is visible. The FSM's lunge is suppressed for
    // ranged creatures (attackCooldown ≈ ∞), so this is their only offense.
    if (isRanged && cfg.rangedTemplate && inCombat && cachedPlayer > 0) {
      s.rangedCdTimer -= dt;
      if (s.rangedCdTimer <= 0) {
        // Only fire with a clear shot — mirrors the LOS gate on acquisition,
        // so a pillar breaks the attack cadence instead of shots through it.
        const seeHero = hasLineOfSight(
          ctx.state,
          Transform.posX[eid],
          Transform.posZ[eid],
          Transform.posX[cachedPlayer],
          Transform.posZ[cachedPlayer]
        );
        if (seeHero) {
          try {
            spawnProjectileFromTemplate(ctx.state, eid, cfg.rangedTemplate, {
              eid: cachedPlayer,
            });
            s.rangedCdTimer = cfg.rangedCooldown ?? 2.0;
            const rangedClip = Array.isArray(cfg.clips.attack)
              ? (cfg.clips.attack[
                  s.attackIdx % cfg.clips.attack.length
                ] as string)
              : cfg.clips.attack;
            if (rangedClip && s.playing !== rangedClip) {
              playClip(s, rangedClip, { loop: false });
            }
          } catch {
            // Template not registered yet (e.g. scene still loading) — retry
            // next cycle without resetting the timer fully.
            s.rangedCdTimer = 0.5;
          }
        } else {
          // No shot this frame; short retry so we fire soon after breaking LOS.
          s.rangedCdTimer = 0.3;
        }
      }
    }

    if (mode === AI_MODE_DEAD || isDead(eid)) {
      handleDeath(ctx, s, eid);
      s.deathTimer -= dt;
      // Last half-second: sink into the ground instead of popping out. XML
      // visuals are driven from WorldTransform, so the sink goes through
      // Transform (+Rigidbody, the physics-owned path); script-owned groups
      // move directly.
      if (s.deathTimer < 0.5 && s.deathTimer > 0) {
        const sink = dt * 1.1;
        if (s.xmlVisual) {
          Transform.posY[eid] -= sink;
          Transform.dirty[eid] = 1;
          if (ctx.state.hasComponent(eid, Rigidbody)) {
            Rigidbody.posY[eid] = Transform.posY[eid];
            markRigidbodyPoseDirty(eid);
          }
        } else if (s.group) {
          s.group.position.y -= sink;
        }
      }
      if (s.deathTimer <= 0) {
        if (s.group) {
          if (!s.xmlVisual) s.group.removeFromParent();
          else s.group.visible = false;
          s.group = null;
        }
        // Remove ECS entity so AI/health/nav stop and packs don't keep a ghost.
        if (ctx.state.exists(eid)) {
          ctx.state.destroyEntity(eid);
        }
      }
      return;
    }

    // Hit flash + hit-reaction clip on HP drop (damage numbers/SFX come from main.ts watcher).
    if (s.flashTimer > 0) {
      s.flashTimer -= dt;
      if (s.flashTimer <= 0 && s.windupElapsed <= 0) applyFlash(s, false);
    }
    if (s.hitTimer > 0) s.hitTimer -= dt;
    const hp = Health.current[eid];
    if (s.lastHp > hp) {
      collectFlashMats(s);
      s.flashTimer = 0.11;
      applyFlash(s, true);
      // Hit-stagger: freeze the FSM (interrupts an in-flight lunge) unless the
      // creature has poise (bosses default to none — see hitStaggerSec).
      const stagger = cfg.hitStaggerSec ?? (cfg.isBoss ? 0 : 0.32);
      if (stagger > 0 && !isDead(eid)) {
        staggerAi(ctx.state, eid, stagger);
      }
      // Reaction, in three grades. A single `hit` clip for every blow reads
      // flat, and swapping the whole body onto it is wrong twice over: it
      // cancels the creature's own swing (so trading blows looks like the mob
      // never attacks) and a boss with poise would get no reaction at all.
      //   heavy blow  → `knockback` (or `hit`), full-body, staggered anyway
      //   poise / mid-swing → additive flinch over whatever is playing
      //   otherwise   → `hit`, full-body, as before
      const dmgFrac = Math.max(
        0,
        (s.lastHp - hp) / Math.max(1, Health.max[eid] || cfg.hp)
      );
      const heavy = dmgFrac >= HEAVY_HIT_FRAC;
      const keepsComposure =
        stagger <= 0 || mode === AI_MODE_LUNGE || s.roarTimer > 0;
      if (s.animator && mode !== AI_MODE_DEAD) {
        const heavyClip = heavy ? (cfg.clips.knockback ?? cfg.clips.hit) : null;
        if (keepsComposure) {
          // Additive: the run/swing underneath keeps playing, the torso recoils.
          const reaction = heavyClip ?? cfg.clips.hit;
          if (reaction) {
            for (const anim of animatorsOf(s)) {
              anim.playFlinch(reaction, {
                weight: heavy ? 0.85 : 0.55,
                release: heavy ? 0.34 : 0.24,
              });
            }
          }
        } else if (heavyClip || cfg.clips.hit) {
          const clipName = heavyClip ?? (cfg.clips.hit as string);
          if (playClip(s, clipName, { loop: false })) {
            // A heavy reaction is longer, and it must not be cut short by the
            // AI clip coming back on the next frame.
            s.hitTimer = heavy ? 0.5 : 0.35;
            // Hold *this* clip for the countdown: keying the hold on
            // `clips.hit` would cut a knockback back to the light reaction on
            // the very next frame.
            s.hitClip = clipName;
            // ±8% so consecutive hits never play back identically.
            jitterTimeScale(s, 0.08);
          }
        }
      }
      spawnParticleBurst(ctx.state, {
        x: Transform.posX[eid],
        y: Transform.posY[eid] + 1.0,
        z: Transform.posZ[eid],
        preset: 'sparks',
        count: heavy ? 12 : 6,
        duration: heavy ? 0.55 : 0.4,
      });
    }
    s.lastHp = hp;

    // Windup telegraph: while the FSM braces for a lunge, ramp an amber→red
    // emissive glow so the attack is dodge-readable. The white hit-flash
    // overrides it; both restore the saved emissive when they end.
    if (AiStateComponent.lungePhase[eid] === AI_LUNGE_PHASE_WINDUP) {
      s.windupElapsed += dt;
      if (s.flashTimer <= 0) {
        collectFlashMats(s);
        const windup = Math.max(
          0.12,
          cfg.lungeWindup ?? AI_DEFAULTS.lungeWindup
        );
        applyWindupGlow(s, Math.min(1, s.windupElapsed / windup));
      }
    } else if (s.windupElapsed > 0) {
      s.windupElapsed = 0;
      if (s.flashTimer <= 0) applyFlash(s, false);
    }

    // FSM / NavMesh own XZ; CCT owns Y. Script never plants / lifts.
    const x = Transform.posX[eid];
    const z = Transform.posZ[eid];
    const visualY = Transform.posY[eid];

    // Facing policy (single writer — navmesh faceVelocity is off):
    //   chase / move → face displacement; attack / lunge → face target.
    // Heading eases toward the target yaw (exponential damping, shortest
    // angular path) — instant snaps are what read as "robotic". Mirrors the
    // hero's dampQ turn (VISUAL_TURN_RATE 10 ≈ tau 0.1s).
    const vx = x - s.prevX;
    const vz = z - s.prevZ;
    const moveSpeed = dt > 0 ? Math.hypot(vx, vz) / dt : 0;
    const yawOff = cfg.facingYawOffset ?? 0;
    const faceTarget = mode === AI_MODE_ATTACK || mode === AI_MODE_LUNGE;
    let targetHeading = s.heading;
    if (faceTarget && cachedPlayer > 0) {
      targetHeading =
        planarYawRadians(
          Transform.posX[cachedPlayer] - x,
          Transform.posZ[cachedPlayer] - z
        ) + yawOff;
    } else if (moveSpeed > MOVE_FACE_SPEED) {
      targetHeading = planarYawRadians(vx, vz) + yawOff;
    }
    if (targetHeading !== s.heading) {
      const turnTau = faceTarget ? 0.09 : 0.14;
      s.heading +=
        shortestAngleDelta(s.heading, targetHeading) *
        (1 - Math.exp(-dt / turnTau));
    }
    s.prevX = x;
    s.prevZ = z;

    if (s.xmlVisual) {
      applyHeadingToTransform(ctx.state, eid, s.heading);
    } else {
      s.group.position.set(x, visualY, z);
      s.group.rotation.set(0, s.heading, 0);
      // Keep Rapier pose in sync when the script owns the scene graph directly.
      applyHeadingToTransform(ctx.state, eid, s.heading);
    }

    if (inCombat) aggroEntities.add(eid);
    else aggroEntities.delete(eid);

    // Clip selection: hit-reaction takes priority (brief stagger).
    // Then AI mode picks the locomotion/combat clip.
    let clip: string;
    if (s.hitTimer > 0 && s.hitClip) {
      clip = s.hitClip;
    } else {
      clip = pickClip(
        s,
        mode,
        moveSpeed > MOVE_FACE_SPEED,
        AiStateComponent.lungePhase[eid] === AI_LUNGE_PHASE_WINDUP
      );
    }
    s.lastPickMode = mode;
    const oneShot =
      clip === cfg.clips.lunge ||
      clip === cfg.clips.hit ||
      clip === cfg.clips.knockback ||
      clip === cfg.clips.windup;
    if (s.animator && s.playing !== clip) {
      if (playClip(s, clip, oneShot ? { loop: false } : undefined) && oneShot) {
        // Swings and telegraphs vary a little in speed; locomotion keeps the
        // rate the gait was authored at (runTimeScale below owns that).
        jitterTimeScale(s, 0.07);
      }
    }
    // Gait rate only applies to gait clips — re-asserting it every frame used
    // to flatten the per-swing jitter back to a constant the frame after.
    if (s.animator && cfg.runTimeScale !== undefined && !oneShot) {
      s.animator.setTimeScale(mode === AI_MODE_CHASE ? cfg.runTimeScale : 1);
    }
  }

  function onDestroy(ctx: MonoBehaviourContext): void {
    const s = presentationMap(ctx.state).get(ctx.entity);
    if (s) {
      s.group?.removeFromParent();
    }
    removeAgent(ctx.state, ctx.entity);
    removeAiInstanceState(ctx.state, ctx.entity);
    AiStateComponent.mode[ctx.entity] = AI_MODE_IDLE;
    AiStateComponent.target[ctx.entity] = 0;
    unregisterEnemy(ctx.entity);
    presentationMap(ctx.state).delete(ctx.entity);
    aggroEntities.delete(ctx.entity);
    bossEntities.delete(ctx.entity);
  }

  return { start, update, onDestroy };
}
