import * as THREE from 'three';
import {
  GltfAnimator,
  GltfPending,
  getGltfRootGroup,
  loadGltfMasterTracked,
  loadGltfToSceneWithAnimator,
  notifyEnemyKilled,
  playSound,
  spawnFloatingText,
} from 'vibegame';
import type { MonoBehaviourContext, State } from 'vibegame';
import {
  Transform,
  Parent,
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
  NavMeshAgent,
  removeAgent,
  planarYawRadians,
  setTransformYawRadians,
  spawnProjectileFromTemplate,
  hasLineOfSight,
} from 'vibegame';
import type { MeleeAiConfig } from 'vibegame';
import {
  registerEnemy,
  setEnemyLabel,
  unregisterEnemy,
} from './enemy-registry';

const TERRAIN_LAYER = 0x0001;
/** Static GLTF / Fixed bodies registered by ``BvhStaticMeshSyncSystem``. */
const STATIC_MESH_LAYER = 0x0002;
/**
 * Extra lift above the sampled surface so skinned bind-pose AABBs and the
 * coarse-vs-rendered height delta don't bury feet in the visible mesh.
 * Tuned against north-exit cobbles (goblins were knee-deep at 0.08).
 */
const FOOT_CLEARANCE = 0.32;

/**
 * Beyond detect range (+ margin) creatures sleep: no AI, no animator, no
 * terrain snap. Wake checks are staggered so a pack far from the hero costs
 * almost nothing while the player is in town.
 */
const SLEEP_RANGE_MARGIN = 8;
const SLEEP_CHECK_INTERVAL = 20;
/** Idle terrain Y refresh cadence (frames) while awake but not in combat. */
const IDLE_GROUND_INTERVAL = 12;

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
  /** Seconds the creature braces (telegraph) before a lunge burst. */
  lungeWindup?: number;
  /** Seconds the lunge burst travels. */
  lungeDuration?: number;
  /** Seconds the creature pauses (vulnerable) after a lunge. */
  lungeRecovery?: number;
  /** Min gap kept between creature and hero during a lunge (anti-overlap). */
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
  /** Time-scale applied to the run clip while chasing (e.g. 1.5 to reuse walk as a jog). */
  runTimeScale?: number;
  /**
   * Uniform visual scale applied to the loaded model (default 1).
   * The asset pipeline (Hunyuan) normalizes every GLB to a ~2-unit bounding
   * box — small pests ship as tall as the hero and the ogre *shorter* — so
   * each creature declares its real in-world size here. footOffset and the
   * health bar are children of the scaled group, so they follow for free.
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
   * When true (default), the creature only acquires the hero when an
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
  /** Preferred stand-off distance from the hero (m). 0 = body-block (legacy). */
  standOffRange?: number;
  /** Distance at which the creature stops fleeing and re-engages (m). */
  reengageRange?: number;
  /** Kite (evade while firing) when the hero is close, vs pure flee. */
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
  /** Visual owned by child GLTFLoader — do not scene-position the group. */
  xmlVisual: boolean;
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
  /** Frames spent waiting for index.html GLTFLoader child. */
  xmlWaitFrames: number;
  /** True while beyond sleep range — AI/nav/anim paused. */
  sleeping: boolean;
  /** Last frame we refreshed terrain Y while idle. */
  lastGroundFrame: number;
  /** Seconds remaining before the next ranged shot (ranged creatures only). */
  rangedCdTimer: number;
}

const playerQuery = defineQuery([PlayerController]);
const xmlVisualQuery = defineQuery([Parent, GltfPending]);
const _box = new THREE.Box3();
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

function groundHeight(
  ctx: MonoBehaviourContext,
  x: number,
  z: number,
  fromY: number
): number {
  // Prefer the *rendered* LOD lattice (what the player sees). The terrain BVH
  // is a coarse 128-seg plane over the whole map and sits below chunk meshes —
  // using it first buried goblins near the north exit path.
  let terrainY = sampleTerrainHeight(ctx.state, x, z);
  if (!Number.isFinite(terrainY) || (terrainY === 0 && Math.abs(fromY) > 2)) {
    terrainY = Number.NaN;
  }
  if (!Number.isFinite(terrainY)) {
    const bvhTerrain = getBvhSurfaceHeight(
      ctx.state,
      x,
      fromY + 60,
      z,
      2000,
      TERRAIN_LAYER
    );
    if (
      bvhTerrain != null &&
      Number.isFinite(bvhTerrain) &&
      !(Math.abs(fromY) > 5 && bvhTerrain < fromY - 20)
    ) {
      terrainY = bvhTerrain;
    }
  }

  // Static props/roads on BVH layer 0x0002: stand on top when higher.
  const propY = getBvhSurfaceHeight(
    ctx.state,
    x,
    fromY + 60,
    z,
    2000,
    STATIC_MESH_LAYER
  );
  let best = terrainY;
  if (
    propY != null &&
    Number.isFinite(propY) &&
    !(Math.abs(fromY) > 5 && propY < fromY - 20)
  ) {
    best = Number.isFinite(best) ? Math.max(best, propY) : propY;
  }
  return Number.isFinite(best) ? best : fromY;
}

/** Feet Y = sampled ground + local foot offset + clearance. */
function feetY(groundY: number, footOffset: number): number {
  return groundY + footOffset + FOOT_CLEARANCE;
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
  // A ranged creature holds a long stand-off and fires projectiles; the FSM is
  // still used for perception + positioning, but the lunge is suppressed (huge
  // cooldown) and the actual damage is dealt by `spawnProjectileFromTemplate`
  // in the update loop. This keeps all the presentation/sleep/loot machinery.
  const isRanged = !!cfg.rangedTemplate;
  // One shared FSM config per creature type. `targetEid` (the hero) is resolved
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
      cfg.attackCooldown ??
      (isRanged ? 9999 : AI_DEFAULTS.attackCooldown),
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
    // Line-of-sight on by default: creatures must actually see the hero before
    // aggroing, instead of beelining through walls. Individual wrappers can
    // opt out (cfg.requireLineOfSight === false) for blind/sense-based mobs.
    requireLineOfSight:
      cfg.requireLineOfSight ?? true,
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

    // Wake check is cheap (XZ only) — always run it so packs aren't stuck
    // asleep for a staggered window while the hero is already in their face.
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

    if (!s.sleeping) {
      s.sleeping = true;
      setNavEnabled(ctx.state, eid, false);
      aggroEntities.delete(eid);
      // Freeze on idle — not mid-jump/lunge — so packs visible past sleep
      // range don't look stuck in a falling pose until the player walks up.
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
    if (s.playing !== cfg.clips.death) {
      playClip(s, cfg.clips.death, { loop: false });
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
    const targets = s.lodAnimators.filter((a): a is GltfAnimator => !!a);
    if (s.animator && !targets.includes(s.animator)) targets.push(s.animator);
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
    return false;
  }

  function applyHeadingToTransform(eid: number, headingRad: number): void {
    // Transform.eulerY is degrees; setTransformYawRadians keeps quat in sync
    // for TransformHierarchySystem / GltfSceneSync (WorldTransform path).
    setTransformYawRadians(Transform, eid, headingRad);
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
    group.updateWorldMatrix(true, true);
    // ``setFromObject`` is a *world* AABB. XML visuals are already placed on
    // terrain, so ``-_box.min.y`` would embed the spawn height (~40) into the
    // foot offset. Adding that back to ``groundY`` cancels the terrain and
    // buries the model near Y=0 the moment sleep→wake starts snapping.
    _box.setFromObject(group);
    const groupWorldY = group.matrixWorld.elements[13] ?? 0;
    const worldMinY = _box.min.y;
    // Local feet offset (never negative — that would bury the model).
    const local = Number.isFinite(worldMinY) ? -(worldMinY - groupWorldY) : 0;
    s.footOffset = Number.isFinite(local) ? Math.max(0, local) : 0;
    if (!s.activated) group.visible = false;
  }

  async function attachLodAnimators(
    state: State,
    s: PresentationState,
    urls: [string, string, string]
  ): Promise<void> {
    if (!s.group) return;
    for (const child of s.group.children) {
      const level = (child.userData.lodLevel as number | undefined) ?? 0;
      if (s.lodAnimators[level]) continue;
      const url = urls[level];
      if (!url) continue;
      try {
        const master = await loadGltfMasterTracked(
          state,
          url,
          level === 0 ? 'critical' : 'background'
        );
        if (!s.group) return;
        const anim = new GltfAnimator(master, {
          root: child,
          crossfadeDuration: 0.25,
        });
        s.lodAnimators[level] = anim;
        if (level === 0 || !s.animator) s.animator = anim;
        if (s.playing) anim.play(s.playing);
      } catch {
        /* lod stream can 404; keep lod0 animator */
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
    if (urls) void attachLodAnimators(ctx.state, s, urls);
    return true;
  }

  function start(ctx: MonoBehaviourContext): void {
    const eid = ctx.entity;
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
      xmlVisual: false,
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
      xmlWaitFrames: preferXml ? 0 : 999,
      sleeping: false,
      lastGroundFrame: -999,
      rangedCdTimer: 0,
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
      if (!s.group && s.xmlWaitFrames < 999) {
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

      // Late-arriving lod1/lod2 children (streamed after near LOD). Attach
      // even while sleeping so a denser LOD doesn't appear in bind/jump pose.
      if (s.group && s.xmlVisual) {
        const urls = deriveLodUrls(cfg.modelUrl);
        if (
          urls &&
          s.group.children.length > s.lodAnimators.filter(Boolean).length
        ) {
          void attachLodAnimators(ctx.state, s, urls);
        }
      }

      // ── Boss gate: stay dormant (hidden, no AI) until the gate opens, then
      //    reveal + intro roar before engaging. ──────────────────────────────
      if (!s.activated) {
        // Cheap staggered poll — don't burn frames while waiting for the gate.
        if (
          (ctx.state.time.frameCount + eid) % SLEEP_CHECK_INTERVAL !== 0
        ) {
          return;
        }
        if (cfg.gateUntil && !cfg.gateUntil()) return;
        s.activated = true;
        if (s.group) s.group.visible = true;
        if (cfg.clips.roar) {
          s.roarTimer = 2.5;
          if (cfg.roarSound) playSound(cfg.roarSound);
        }
      } else if (!shouldSimulate(ctx, s, eid)) {
        // Sleeping: park on idle once so frozen jump/lunge poses don't linger.
        if (s.group && s.playing !== cfg.clips.idle) {
          playClip(s, cfg.clips.idle);
          for (const anim of s.lodAnimators) anim?.update(0);
        }
        return;
      }

      // Ensure the FSM always has the hero as explicit target while awake.
      resolvePlayer(ctx);

      if (s.roarTimer > 0 && s.group) {
        s.roarTimer -= ctx.deltaTime;
        for (const anim of s.lodAnimators) anim?.update(ctx.deltaTime);
        if (cfg.clips.roar && s.playing !== cfg.clips.roar) {
          playClip(s, cfg.clips.roar, { loop: false });
        }
        const rx = Transform.posX[eid];
        const rz = Transform.posZ[eid];
        const ry = groundHeight(ctx, rx, rz, Transform.posY[eid]);
        if (Number.isFinite(ry)) {
          const fy = feetY(ry, s.footOffset);
          Transform.posY[eid] = fy;
          if (!s.xmlVisual) {
            s.group.position.set(rx, fy, rz);
          }
        }
        return;
      }

      // ── AI (engine FSM): perception, FSM, navmesh steering, attack damage.
      const inst = getOrCreateAiInstanceState(ctx.state, eid);
      runMeleeAiFrame(ctx.state, eid, meleeConfig, inst);
      // Agent may be attached on first AI tick — reclaim yaw ownership.
      claimFacingOwnership(ctx.state, eid);

      // Presentation: visuals, clips, terrain-Y, hit-flash, death FX + loot.
      if (!s.group) return;
      for (const anim of s.lodAnimators) anim?.update(ctx.deltaTime);
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
      // engaged and the hero is visible. The FSM's lunge is suppressed for
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
              spawnProjectileFromTemplate(
                ctx.state,
                eid,
                cfg.rangedTemplate,
                { eid: cachedPlayer }
              );
              s.rangedCdTimer = cfg.rangedCooldown ?? 2.0;
              if (cfg.clips.attack && s.playing !== cfg.clips.attack) {
                playClip(s, cfg.clips.attack, { loop: false });
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
        if (s.deathTimer <= 0) {
          if (!s.xmlVisual) s.group.removeFromParent();
          else s.group.visible = false;
          s.group = null;
        }
        return;
      }

      if (!s.ready) {
        const gy = groundHeight(
          ctx,
          Transform.posX[eid],
          Transform.posZ[eid],
          Transform.posY[eid] || 500
        );
        if (!Number.isFinite(gy)) return;
        Transform.posY[eid] = feetY(gy, s.footOffset);
        Transform.dirty[eid] = 1;
        s.ready = true;
        s.lastGroundFrame = ctx.state.time.frameCount;
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
      // the visual transform. Sampling is adaptive: while moving we snap every
      // frame (a creature crossing a slope at chase speed would otherwise float
      // for up to IDLE_GROUND_INTERVAL frames); while parked we throttle to
      // IDLE_GROUND_INTERVAL to save the BVH raycast cost.
      const x = Transform.posX[eid];
      const z = Transform.posZ[eid];
      const frame = ctx.state.time.frameCount;
      const planarStep = Math.hypot(x - s.prevX, z - s.prevZ);
      const needGround =
        inCombat ||
        !s.ready ||
        planarStep > 0.02 ||
        frame - s.lastGroundFrame >= IDLE_GROUND_INTERVAL;
      let visualY = Transform.posY[eid];
      if (needGround) {
        const groundY = groundHeight(ctx, x, z, Transform.posY[eid]);
        visualY = Number.isFinite(groundY)
          ? feetY(groundY, s.footOffset)
          : Transform.posY[eid];
        Transform.posY[eid] = visualY;
        Transform.dirty[eid] = 1;
        s.lastGroundFrame = frame;
      } else {
        visualY = Transform.posY[eid];
      }

      // Facing policy (single writer — navmesh faceVelocity is off):
      //   chase / move → face displacement; attack / lunge → face target.
      const vx = x - s.prevX;
      const vz = z - s.prevZ;
      const moveSpeed = dt > 0 ? Math.hypot(vx, vz) / dt : 0;
      const yawOff = cfg.facingYawOffset ?? 0;
      const faceTarget =
        mode === AI_MODE_ATTACK || mode === AI_MODE_LUNGE;
      if (faceTarget && cachedPlayer > 0) {
        s.heading =
          planarYawRadians(
            Transform.posX[cachedPlayer] - x,
            Transform.posZ[cachedPlayer] - z
          ) + yawOff;
      } else if (moveSpeed > MOVE_FACE_SPEED) {
        s.heading = planarYawRadians(vx, vz) + yawOff;
      }
      s.prevX = x;
      s.prevZ = z;

      if (s.xmlVisual) {
        applyHeadingToTransform(eid, s.heading);
      } else {
        s.group.position.set(x, visualY, z);
        s.group.rotation.set(0, s.heading, 0);
      }

      if (inCombat) aggroEntities.add(eid);
      else aggroEntities.delete(eid);

      // Clip selection: hit-reaction takes priority (brief stagger).
      // Then AI mode picks the locomotion/combat clip.
      let clip: string;
      if (s.hitTimer > 0 && cfg.clips.hit) {
        clip = cfg.clips.hit;
      } else {
        clip = pickClip(mode, moveSpeed > MOVE_FACE_SPEED);
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
  }

  return { start, update, onDestroy };
}
