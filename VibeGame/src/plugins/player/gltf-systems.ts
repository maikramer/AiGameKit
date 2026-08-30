import { logger } from '../../core/utils/logger';
import { LineSegments, Quaternion, Vector3 } from 'three';
import { dampQ } from 'maath/easing';
import { defineQuery, type Adapter, type State, type System } from '../../core';
import {
  loadGltfAnimated,
  loadGltfAnimatedForEntity,
} from '../../extras/gltf-bridge';
import { GltfAnimator, matchClipKeyword } from '../../extras/gltf-animator';
import {
  WeaponTrail,
  bladeEndpoints,
  type WeaponTrailOptions,
} from '../../extras/weapon-trail';
import { getScene } from '../rendering/utils';
import {
  getAnimator,
  registerAnimator,
  unregisterAnimator,
} from '../gltf-anim/systems';
import { HasAnimator } from '../animation/components';
import { InputState } from '../input/components';
import { isKeyDown } from '../input/utils';
import {
  findNearestInteractionTarget,
  resolveInteractionGesture,
} from '../hud/interaction-targets';
import {
  CharacterController,
  CharacterMovement,
  Collider,
  getBodyForEntity,
  getBodyYForFeetAt,
  getCharacterFeetY,
  invalidateCollider,
  markRigidbodyPoseDirty,
  Rigidbody,
} from '../physics';
import { applyPlayerColliderFromAabb } from './player-collider-fit';
import { computePlayerFootAnchor } from './player-foot-anchor';
import { Transform, WorldTransform } from '../transforms';
import { PlayerController, PlayerGltfConfig } from './components';
import { PLAYER_COLLIDER_DEFAULTS } from './constants';
import { damageHealth, Health } from '../combat/components';
import { defineSystem } from '../../core';

let nextModelUrlIndex = 1;
const modelUrlByIndex = new Map<number, string>();

const inFlightByState = new WeakMap<State, Set<number>>();
const yOffsetByState = new WeakMap<State, Map<number, number>>();

function assignPlayerGltfModelUrl(url: string): number {
  const idx = nextModelUrlIndex++;
  modelUrlByIndex.set(idx, url.trim());
  return idx;
}

export const playerGltfModelUrlAdapter: Adapter = (entity, value, _state) => {
  PlayerGltfConfig.modelUrlIndex[entity] = assignPlayerGltfModelUrl(value);
};

function getModelUrl(index: number): string | undefined {
  return modelUrlByIndex.get(index);
}

function isLoadInFlight(state: State, eid: number): boolean {
  return inFlightByState.get(state)?.has(eid) ?? false;
}

function setLoadInFlight(state: State, eid: number, v: boolean): void {
  let s = inFlightByState.get(state);
  if (!s) {
    s = new Set();
    inFlightByState.set(state, s);
  }
  if (v) {
    s.add(eid);
  } else {
    s.delete(eid);
  }
}

function getYOffset(state: State, eid: number): number {
  return yOffsetByState.get(state)?.get(eid) ?? 0;
}

function setYOffset(state: State, eid: number, y: number): void {
  let m = yOffsetByState.get(state);
  if (!m) {
    m = new Map();
    yOffsetByState.set(state, m);
  }
  m.set(eid, y);
}

const DEFAULT_LOCOMOTION_SET = 'default';

const ATTACK_RANGE = 3.0; // m — forgiving reach so swings connect
const ATTACK_VERTICAL = 2.5; // m — swing cone is local; no hits through floors
const ATTACK_DAMAGE = 25;
// Damage of the engine's built-in melee hit. Games that own their swing
// damage (crit/backstab/bonus — see simple-rpg `src/game/melee.ts`) set this
// to 0 so the two paths don't double-dip on the same blow. The attack clip
// and the Destructible harvest are unaffected either way.
let playerMeleeDamage = ATTACK_DAMAGE;

/**
 * Playback speed of the player's attack clips. Quaternius-style packs swing
 * in ~1.5s with a long settle; values > 1 tighten the attack into a snappier
 * read. Impact scheduling (engine `pendingMelee` and the game's own swing
 * delay) must divide the clip-relative delay by this so the blow still lands
 * on the strike peak — use {@link getPlayerAttackTimeScale}.
 */
let attackTimeScale = 1.4;

/** Override the player's attack-clip playback speed (clamped to >= 0.1). */
export function setPlayerAttackTimeScale(scale: number): void {
  attackTimeScale = Math.max(0.1, scale);
}

/** Current attack-clip playback speed (default 1.4). */
export function getPlayerAttackTimeScale(): number {
  return attackTimeScale;
}
// Fraction of the attack clip after which the blow lands. Quaternius-style
// packs peak the cut ~25–40% in (then long recovery); 0.7 lands in the settle.
const ATTACK_IMPACT_FRACTION = 0.35;
// Fallback impact delay when the clip duration is unknown.
const ATTACK_IMPACT_FALLBACK = 0.22; // s
/** Speed-up for the bend-down gather clip (~40f @24fps → ~0.9s at 1.85×). */
const GATHER_TIME_SCALE = 1.85;
const prevPrimary = new Map<number, number>();
// Per-attacker countdown until the pending melee blow lands (seconds).
const pendingMelee = new Map<number, number>();
const prevInteract = new Map<number, number>();
/** Last registered locomotion clip signature — skip re-register when unchanged. */
const prevLocoSig = new Map<number, string>();

// Context hint for the attack clip: the game sets a keyword (e.g. 'mine',
// 'chop', 'sword', 'axe', 'spear') based on tool/target; the attack picks the
// matching clip, falling back to a generic 'attack' swing. A string[] is a
// combo pool — successive attacks advance through it per `comboMode`.
let attackClipHint: string | string[] | null = null;
// Next index into the combo pool (advances after each landed strike).
let attackComboIdx = 0;
/**
 * How a combo pool advances per strike.
 * - `cycle` — 0,1,2,… (fixed sequence)
 * - `random` — any entry except the one just played
 * - `alternating` — bases are played in random order, and every strike
 *   swaps the swing side by appending the `_m` mirrored variant (built
 *   on demand by the animator) — reads as a natural left↔right chain.
 */
let comboMode: PlayerAttackComboMode = 'cycle';
/** Pre-computed keyword of the NEXT strike (pure reads via getPlayerAttackClip). */
let nextComboKeyword: string | null = null;
/** Side flag for `alternating` (true = mirrored `_m` swing). */
let comboSide = false;
/** Index/base just played — `random`/`alternating` avoid repeating it. */
let lastComboPick = -1;

// Context hint for the idle clip: when the hero has a weapon/tool equipped, the
// game sets a keyword (e.g. 'swordidle', 'axeidle') to use a combat-guard idle
// instead of the default relaxed idle. null = use default idle.
let idleClipHint: string | null = null;

export type PlayerAttackComboMode = 'cycle' | 'random' | 'alternating';

export interface PlayerAttackComboOptions {
  /** Pool advance policy. Default `cycle`. */
  mode?: PlayerAttackComboMode;
}

/** Random int in [0, n) that avoids `avoid` when possible (n > 1). */
function pickComboIndex(n: number, avoid: number): number {
  if (n <= 0) return 0;
  if (n === 1) return 0;
  let i = avoid;
  while (i === avoid) i = Math.floor(Math.random() * n);
  return i;
}

/** (Re)compute the keyword the next strike will play, honouring `comboMode`. */
function refreshNextComboKeyword(): void {
  if (!Array.isArray(attackClipHint) || attackClipHint.length === 0) {
    nextComboKeyword = null;
    return;
  }
  const pool = attackClipHint;
  if (comboMode === 'cycle') {
    nextComboKeyword = pool[attackComboIdx % pool.length] ?? null;
    return;
  }
  if (comboMode === 'random') {
    lastComboPick = pickComboIndex(pool.length, lastComboPick);
    nextComboKeyword = pool[lastComboPick] ?? null;
    return;
  }
  // alternating: random base (not the last one), side flips every strike.
  lastComboPick = pickComboIndex(pool.length, lastComboPick);
  comboSide = !comboSide;
  const base = pool[lastComboPick] ?? '';
  nextComboKeyword = comboSide ? `${base}_m` : base;
}

/** Choose which attack animation the player plays next. Pass a keyword
 * (e.g. 'sword'), a combo pool (e.g. ['sword', 'sworda', 'swordb']) with an
 * advance `mode` (`cycle` default, `random`, or `alternating` mirrored
 * left↔right chains), or null for the generic attack/swing clip. Re-registering
 * the same hint is a no-op (does not reset an in-progress combo). */
export function setPlayerAttackClip(
  hint: string | string[] | null,
  opts?: PlayerAttackComboOptions
): void {
  const mode = opts?.mode ?? 'cycle';
  if (
    hint === attackClipHint &&
    mode === comboMode &&
    nextComboKeyword !== null
  ) {
    return; // same pool + mode: keep the combo position
  }
  attackClipHint = hint;
  comboMode = mode;
  attackComboIdx = 0;
  comboSide = true; // first alternating strike flips to the un-mirrored base
  lastComboPick = -1;
  refreshNextComboKeyword();
}

/**
 * Advance the combo after a strike (exported for tests). `advanced` is true
 * only when the hinted clip actually played — a generic-swing fallback must
 * not burn a combo step.
 */
export function advancePlayerAttackCombo(advanced: boolean): void {
  if (!advanced) return;
  if (!Array.isArray(attackClipHint) || attackClipHint.length <= 1) return;
  if (comboMode === 'cycle') {
    attackComboIdx = (attackComboIdx + 1) % attackClipHint.length;
  }
  refreshNextComboKeyword();
}

/** Resolve the keyword the NEXT strike will try to play (combo-aware). */
function nextAttackKeyword(): string | null {
  if (!attackClipHint) return null;
  if (typeof attackClipHint === 'string') return attackClipHint;
  return nextComboKeyword;
}

/** Current attack-clip keyword (e.g. ``sword`` / ``chop``), or null. With a
 * combo pool, returns the keyword of the upcoming strike. */
export function getPlayerAttackClip(): string | null {
  return nextAttackKeyword();
}

/** Override the idle clip (e.g. 'swordidle' for a combat-guard stance when a
 * weapon is equipped). Pass null to revert to the default idle. */
export function setPlayerIdleClip(hint: string | null): void {
  idleClipHint = hint;
}

// ── Held item (weapon/tool in the hand) ──────────────────────────────────────
export interface HeldItemGrip {
  x: number;
  y: number;
  z: number;
  rx: number;
  ry: number;
  rz: number;
  scale: number;
}
const DEFAULT_GRIP: HeldItemGrip = {
  x: 0,
  y: 0,
  z: 0,
  rx: 0,
  ry: 0,
  rz: 0,
  scale: 1,
};

let heldItemUrl: string | null = null;
let heldGrip: HeldItemGrip = DEFAULT_GRIP;
let heldObj: import('three').Object3D | null = null;
let heldCurrentUrl: string | null = null;
const heldCache = new Map<string, import('three').Object3D>();
const heldLoading = new Set<string>();

/** Attach a GLB to the hero's hand (follows the animation). null = empty hand.
 * `grip` is the local offset/rotation/scale on the right-hand bone. */
export function setPlayerHeldItem(
  url: string | null,
  grip?: Partial<HeldItemGrip>
): void {
  heldItemUrl = url;
  heldGrip = grip ? { ...DEFAULT_GRIP, ...grip } : DEFAULT_GRIP;
}

// ── Weapon trail (ribbon swept by the held weapon during a swing) ────────────
// A swing lasts ~0.2s: without a trail the arc is gone before the eye finds
// it, and the impact particles that follow say nothing about where the blade
// went. Enabled by default and only ever visible while an attack override is
// running with something in hand — pass `false` to opt out.
let weaponTrailOptions: WeaponTrailOptions | null = {};
let weaponTrail: WeaponTrail | null = null;
/** True between the start of an attack override and its 'finished' event. */
let swingActive = false;

/**
 * Configure (or disable) the trail the player's held weapon sweeps while
 * attacking. `false` removes it; an options object rebuilds it with new
 * colour/length/opacity on the next swing.
 */
export function setPlayerWeaponTrail(
  options: WeaponTrailOptions | false
): void {
  weaponTrailOptions = options === false ? null : options;
  if (weaponTrail) {
    weaponTrail.dispose();
    weaponTrail = null;
  }
}

function updateWeaponTrail(state: State, swinging: boolean): void {
  if (!weaponTrailOptions) return;
  const now = state.time.elapsed;

  if (!swinging || !heldObj) {
    // Keep ageing so the tail dissolves after the swing instead of hanging in
    // the air until the next one.
    if (weaponTrail && weaponTrail.sampleCount > 0) weaponTrail.update(now);
    return;
  }

  // Overshoot the modelled tip a little: the ribbon then reads as the edge's
  // sweep rather than a decal glued to the blade mesh.
  const ends = bladeEndpoints(heldObj, { extend: 0.12, inset: 0.45 });
  if (!ends) return;

  const scene = getScene(state);
  if (!scene) return;
  if (!weaponTrail) weaponTrail = new WeaponTrail(weaponTrailOptions);
  // Re-parent after a scene swap (runtime teardown drops the old scene's
  // children), otherwise the ribbon silently stops rendering.
  if (weaponTrail.object3D.parent !== scene) scene.add(weaponTrail.object3D);

  weaponTrail.push(ends.base, ends.tip, now);
}

// ── Aim facing: while set, the visual body yaws toward (x,z) instead of the
//    movement heading (used for bomb aim). ─────────────────────────────────────
let faceTargetX: number | null = null;
let faceTargetZ = 0;
const _yAxis = new Vector3(0, 1, 0);

/** Turn the hero's body to face a world point (e.g. bomb aim). null clears. */
export function setPlayerFaceTarget(x: number | null, z = 0): void {
  faceTargetX = x;
  faceTargetZ = z;
}

/**
 * Resolve the hero's right-hand bone across skeleton naming conventions.
 * Different asset pipelines name it differently — Quaternius/UMA rigs use
 * `RightHand`, the retargeted/pool heroes use Mixamo-ish `hand_r` — and a
 * held weapon silently vanishes when the hard-coded name misses.
 */
const HAND_BONE_CANDIDATES = [
  'RightHand',
  'hand_r',
  'Hand_R',
  'right_hand',
  'righthand',
] as const;

let handBoneNameCache: string | null = null;

/**
 * Resolve an object's right-hand bone across skeleton naming conventions
 * (`RightHand` Quaternius/UMA, `hand_r` Mixamo-style pool heroes, plus a
 * fuzzy fallback). Exported for games/tests; the held-item system uses it to
 * keep weapons attached regardless of which pipeline produced the hero GLB.
 */
export function findRightHandBone(
  root: import('three').Object3D
): import('three').Object3D | null {
  if (handBoneNameCache) {
    const cached = root.getObjectByName(handBoneNameCache);
    if (cached) return cached;
    handBoneNameCache = null;
  }
  for (const name of HAND_BONE_CANDIDATES) {
    const bone = root.getObjectByName(name);
    if (bone) {
      handBoneNameCache = name;
      return bone;
    }
  }
  // Fuzzy last resort: a bone named like a right hand but not a finger
  // (covers `R_hand`, `thumb_01_r`-style rigs without listing every scheme).
  const found: { bone: import('three').Object3D | null } = { bone: null };
  root.traverse((o) => {
    if (found.bone || !o.name) return;
    const n = o.name.toLowerCase();
    if (!n.includes('hand')) return;
    if (n.includes('finger') || n.includes('thumb')) return;
    if (n.includes('_r') || n.startsWith('r_') || n.includes('right')) {
      found.bone = o;
    }
  });
  if (found.bone) handBoneNameCache = found.bone.name;
  return found.bone;
}

function applyHeldItem(animator: GltfAnimator, state: State): void {
  // Swap the attached model when the requested url changes.
  if (heldCurrentUrl !== heldItemUrl) {
    if (heldObj) {
      heldObj.removeFromParent();
      heldObj = null;
    }
    heldCurrentUrl = null;
    const want = heldItemUrl;
    if (want) {
      const bone = findRightHandBone(animator.root);
      if (!bone) return; // skeleton not ready — retry next frame
      const cached = heldCache.get(want);
      if (cached) {
        bone.add(cached); // reparent to the hand (three converts world→local)
        cached.visible = true;
        heldObj = cached;
        heldCurrentUrl = want;
      } else if (!heldLoading.has(want)) {
        heldLoading.add(want);
        void loadGltfAnimated(state, want)
          .then((gltf) => {
            heldLoading.delete(want);
            gltf.scene.removeFromParent(); // we parent it to the bone instead
            heldCache.set(want, gltf.scene);
          })
          .catch((err) => {
            heldLoading.delete(want);
            // A wrong URL silently leaves the hand empty — log it once per
            // url so a moved/missing GLB is findable in the console.
            console.warn('[player-gltf] held-item load failed', want, err);
          });
      }
    }
  }
  // Re-apply the grip every frame so live grip edits (and any future
  // per-frame grip animation) take effect without a url swap.
  if (heldObj) {
    heldObj.position.set(heldGrip.x, heldGrip.y, heldGrip.z);
    heldObj.rotation.set(heldGrip.rx, heldGrip.ry, heldGrip.rz);
    heldObj.scale.setScalar(heldGrip.scale);
  }
}

// Natural stride speed (m/s) the gait clips were authored at — playback is
// time-scaled by actualSpeed/ref so the feet track the ground.
const WALK_CLIP_SPEED = 1.6;
const RUN_CLIP_SPEED = 2.8;
// Visual-only yaw smoothing rate (1/s) for the skinned root; the physics
// heading still turns at PlayerController.rotationSpeed.
const VISUAL_TURN_RATE = 10;
const _visualQuat = new Quaternion();

const _fwd = new Vector3();
const _q = new Quaternion();

/** Best clip match for any keyword (exact / separator / shortest substring). */
function findClip(animator: GltfAnimator, ...keywords: string[]): string {
  for (const k of keywords) {
    const hit = matchClipKeyword(animator.clipNames, k);
    if (hit) return hit;
  }
  return '';
}

/** Fuzzy clip search: tries progressively relaxed matching strategies. */
function findClipFuzzy(animator: GltfAnimator, ...keywords: string[]): string {
  // Strategy 1: exact keyword containment (original)
  const direct = findClip(animator, ...keywords);
  if (direct) return direct;

  const names = animator.clipNames;
  const lower = animator.clipNamesLower;

  // Strategy 2: check for common animation naming variants
  const variants: Record<string, string[]> = {
    walk: [
      'locomotion',
      'motion',
      'move',
      'jog',
      'stride',
      'walk_cycle',
      'walking',
    ],
    run: ['sprint', 'fast', 'run_cycle', 'running'],
    jump: ['leap', 'hop', 'vault', 'jump_start', 'jump_up', 'jumping'],
    fall: ['airborne', 'descent', 'falling', 'drop', 'idle_fall'],
    idle: ['stand', 'rest', 'pose', 'wait', 'breath', 'idle_a', 'idle_b'],
    turnleft: ['turn_left', 'turnleft', 'pivot_left', 'turnl'],
    turnright: ['turn_right', 'turnright', 'pivot_right', 'turnr'],
    back: ['walk_back', 'walkback', 'backward', 'reverse', 'back'],
  };

  for (const k of keywords) {
    const alts = variants[k] ?? [];
    for (const alt of alts) {
      const idx = lower.findIndex((n) => n.includes(alt));
      if (idx >= 0) return names[idx];
    }
  }

  return '';
}

interface Locomotion {
  idle: string;
  walk: string;
  run: string;
  jump: string;
  fall: string;
  turnLeft: string;
  turnRight: string;
  back: string;
}

const locomotionCache = new WeakMap<State, Map<number, Locomotion>>();

function getLocomotionMap(state: State): Map<number, Locomotion> {
  let m = locomotionCache.get(state);
  if (!m) {
    m = new Map();
    locomotionCache.set(state, m);
  }
  return m;
}

function clearLocomotionCache(state: State, eid: number): void {
  locomotionCache.get(state)?.delete(eid);
}

/** Resolve clips by explicit index override (>0) else by name keyword (fuzzy).
 *  Memoized per-eid; clear via {@link clearLocomotionCache} on animator swap. */
function resolveLocomotion(
  state: State,
  animator: GltfAnimator,
  eid: number
): Locomotion {
  const cache = getLocomotionMap(state);
  const cached = cache.get(eid);
  if (cached) return cached;

  const names = animator.clipNames;
  const byIndex = (field: number): string =>
    field > 0 && field < names.length ? names[field] : '';
  const resolved: Locomotion = {
    idle:
      byIndex(PlayerGltfConfig.idleClipIndex[eid]) ||
      findClipFuzzy(animator, 'idle', 'breathe'),
    walk:
      byIndex(PlayerGltfConfig.walkClipIndex[eid]) ||
      findClipFuzzy(animator, 'walk'),
    run:
      byIndex(PlayerGltfConfig.runClipIndex[eid]) ||
      findClipFuzzy(animator, 'run'),
    jump: findClipFuzzy(animator, 'jump'),
    fall: findClipFuzzy(animator, 'fall'),
    turnLeft: findClipFuzzy(animator, 'turnleft'),
    turnRight: findClipFuzzy(animator, 'turnright'),
    back: findClipFuzzy(animator, 'back'),
  };
  cache.set(eid, resolved);
  return resolved;
}

function isRunModifier(): boolean {
  return isKeyDown('ShiftLeft') || isKeyDown('ShiftRight');
}

/** Damage dealt by the engine's built-in primary-action melee hit. Pass 0 to
 * disable it entirely (the game's own swing code becomes the sole damage
 * source); values > 0 override the default flat damage. */
export function setPlayerMeleeDamage(dmg: number): void {
  playerMeleeDamage = Math.max(0, dmg);
}

/** Damage dealt by the engine's built-in melee hit (0 = disabled). */
export function getPlayerMeleeDamage(): number {
  return playerMeleeDamage;
}

let meleeQuery: ReturnType<typeof defineQuery> | null = null;

/** Damage Health entities within a forward cone when an attack lands. */
function meleeHit(state: State, attacker: number): void {
  if (playerMeleeDamage <= 0) return;
  if (!state.hasComponent(attacker, WorldTransform)) return;
  if (!meleeQuery) meleeQuery = defineQuery([Health, WorldTransform]);

  const ax = WorldTransform.posX[attacker];
  const ay = WorldTransform.posY[attacker];
  const az = WorldTransform.posZ[attacker];
  _fwd
    .set(0, 0, 1)
    .applyQuaternion(
      _q.set(
        WorldTransform.rotX[attacker],
        WorldTransform.rotY[attacker],
        WorldTransform.rotZ[attacker],
        WorldTransform.rotW[attacker]
      )
    );

  for (const target of meleeQuery(state.world)) {
    if (target === attacker) continue;
    // World space on both sides: a nested target's local Transform would
    // measure against the attacker's world position and whiff/hit ghosts.
    const dx = WorldTransform.posX[target] - ax;
    const dy = WorldTransform.posY[target] - ay;
    const dz = WorldTransform.posZ[target] - az;
    const dist = Math.hypot(dx, dz);
    if (dist > ATTACK_RANGE || dist < 0.001) continue;
    if (Math.abs(dy) > ATTACK_VERTICAL) continue; // no hits through floors
    // in front hemisphere (~90° each side) — forgiving so the swing lands
    // without pixel-perfect facing.
    if ((dx * _fwd.x + dz * _fwd.z) / dist < 0.0) continue;
    // Route through the combat helper so COMBAT_DAMAGED / COMBAT_KILLED fire
    // and death-triggered systems (save-state, quest kills, death FX) see the
    // player's attacks. The old inline write bypassed all of that.
    damageHealth(target, playerMeleeDamage, attacker);
  }
}

const playerGltfSetupQuery = defineQuery([PlayerController, PlayerGltfConfig]);

/** Runs in the first setup bucket so {@link HasAnimator} exists before the procedural character is spawned. */
export const PlayerGltfEnsureHasAnimatorSystem: System = defineSystem({
  name: 'PlayerGltfEnsureHasAnimatorSystem',
  group: 'setup',
  first: true,
  update: (state) => {
    for (const eid of playerGltfSetupQuery(state.world)) {
      if (!state.hasComponent(eid, HasAnimator)) {
        state.addComponent(eid, HasAnimator);
      }
    }
  },
});

export const PlayerGltfSetupSystem: System = defineSystem({
  name: 'PlayerGltfSetupSystem',
  group: 'draw',
  update: (state) => {
    for (const eid of playerGltfSetupQuery(state.world)) {
      if (PlayerGltfConfig.loaded[eid] !== 0) {
        continue;
      }
      if (isLoadInFlight(state, eid)) {
        continue;
      }

      const urlIndex = PlayerGltfConfig.modelUrlIndex[eid];
      const url = urlIndex > 0 ? getModelUrl(urlIndex) : undefined;
      if (!url) {
        PlayerGltfConfig.loaded[eid] = 1;
        continue;
      }

      setLoadInFlight(state, eid, true);

      // Register teardown unconditionally so cleanup runs even if the GLB
      // load fails — otherwise the eid-keyed maps leak on load error.
      state.onDestroy(eid, () => {
        const idx = PlayerGltfConfig.animatorRegistryIndex[eid];
        if (idx !== 0) unregisterAnimator(state, idx);
        prevPrimary.delete(eid);
        pendingMelee.delete(eid);
        prevInteract.delete(eid);
        prevLocoSig.delete(eid);
        // Death mid-swing: the override's 'finished' never fires, so the trail
        // flag must not outlive the attacker (a later override would sweep a
        // ribbon with no weapon moving).
        swingActive = false;
        clearLocomotionCache(state, eid);
      });

      void loadGltfAnimatedForEntity(state, url, eid)
        .then((gltf) => {
          // Entity may have been destroyed while the model loaded — don't leak
          // an orphan animator into the registry.
          if (!state.exists(eid)) return;

          // Anchor on soles (ball/toe bones), not pelvis — skinned avatars often
          // keep the armature root at the waist; raw Box3 alone can plant the hip.
          const prevFeetY = state.hasComponent(eid, Collider)
            ? getCharacterFeetY(state, eid, Transform.posY[eid])
            : Transform.posY[eid];

          const anchor = computePlayerFootAnchor(gltf.scene);
          const { box, yOffset } = anchor;
          setYOffset(state, eid, yOffset);

          if (state.hasComponent(eid, Collider)) {
            const tsx = state.hasComponent(eid, Transform)
              ? Math.max(Math.abs(Transform.scaleX[eid]), 1e-6)
              : 1;
            const tsy = state.hasComponent(eid, Transform)
              ? Math.max(Math.abs(Transform.scaleY[eid]), 1e-6)
              : 1;
            const tsz = state.hasComponent(eid, Transform)
              ? Math.max(Math.abs(Transform.scaleZ[eid]), 1e-6)
              : 1;
            const fit = applyPlayerColliderFromAabb({
              box,
              yOffset,
              margin: 0.02,
              scaleX: tsx,
              scaleY: tsy,
              scaleZ: tsz,
            });
            Collider.shape[eid] = fit.shape;
            Collider.radius[eid] = fit.radius;
            Collider.height[eid] = fit.height;
            Collider.sizeX[eid] = fit.sizeX;
            Collider.sizeY[eid] = fit.sizeY;
            Collider.sizeZ[eid] = fit.sizeZ;
            Collider.posOffsetX[eid] = fit.posOffsetX;
            Collider.posOffsetY[eid] = fit.posOffsetY;
            Collider.posOffsetZ[eid] = fit.posOffsetZ;
            invalidateCollider(state, eid);

            // Keep soles on the same world Y after the capsule refit (defaults
            // used offsetY=0.75 before the GLB AABB was known).
            const newBodyY = getBodyYForFeetAt(state, eid, prevFeetY);
            Transform.posY[eid] = newBodyY;
            Transform.dirty[eid] = 1;
            if (state.hasComponent(eid, Rigidbody)) {
              Rigidbody.posY[eid] = newBodyY;
              markRigidbodyPoseDirty(eid);
              const body = getBodyForEntity(state, eid);
              if (body) {
                const t = body.translation();
                body.setTranslation({ x: t.x, y: newBodyY, z: t.z }, true);
              }
            }
          }

          const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });
          const regIdx = registerAnimator(state, animator);
          PlayerGltfConfig.animatorRegistryIndex[eid] = regIdx;

          const loco = resolveLocomotion(state, animator, eid);
          if (loco.idle && loco.walk && loco.run) {
            animator.registerLocomotionSet(DEFAULT_LOCOMOTION_SET, {
              idle: loco.idle,
              walk: loco.walk,
              run: loco.run,
              jump: loco.jump || undefined,
            });
          }
          animator.play(loco.idle || animator.clipNames[0] || '');
        })
        .catch((err: unknown) => {
          logger.error('[player-gltf] load failed', err);
        })
        .finally(() => {
          PlayerGltfConfig.loaded[eid] = 1;
          setLoadInFlight(state, eid, false);
        });
    }
  },
});

const playerGltfAnimQuery = defineQuery([
  PlayerController,
  PlayerGltfConfig,
  InputState,
]);

function ensureDebugCapsule(_state: State): LineSegments | null {
  return null;
}

export const PlayerGltfAnimStateSystem: System = defineSystem({
  name: 'PlayerGltfAnimStateSystem',
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime;

    for (const eid of playerGltfAnimQuery(state.world)) {
      if (PlayerGltfConfig.loaded[eid] !== 1) {
        continue;
      }
      const regIdx = PlayerGltfConfig.animatorRegistryIndex[eid];
      if (regIdx === 0) {
        continue;
      }

      const animator = getAnimator(state, regIdx);
      if (!animator) {
        continue;
      }

      // Keep the held weapon/tool attached to the hand bone (follows the rig).
      applyHeldItem(animator, state);
      // The trail samples the blade *after* the attach, so the first frame of a
      // swing already has the hand's pose (a frame-late sample starts the
      // ribbon behind the blade).
      updateWeaponTrail(state, swingActive && animator.overrideLock);

      const grounded =
        !state.hasComponent(eid, CharacterController) ||
        CharacterController.grounded[eid] === 1;
      const vy = state.hasComponent(eid, CharacterMovement)
        ? CharacterMovement.velocityY[eid]
        : 0;

      // Attack: rising edge of primary action (left click) while grounded plays
      // the skeletal attack clip as a one-shot override (locks locomotion until
      // it finishes), and lands a melee hit.
      const primary =
        InputState.primaryAction[eid] || InputState.leftMouse[eid];
      const wasPrimary = prevPrimary.get(eid) ?? 0;
      prevPrimary.set(eid, primary);
      if (primary && !wasPrimary && grounded && !animator.overrideLock) {
        // Tool/context clip first (mine/chop/sword/axe/spear), else a generic
        // swing. Don't gate the hit on the clip existing. "<clip>_m" hints are
        // mirrored variants (alternating-hand combos) — built here on demand.
        const hintKeyword = nextAttackKeyword();
        if (hintKeyword?.endsWith('_m'))
          animator.ensureMirroredClip(hintKeyword);
        const hinted = hintKeyword ? findClipFuzzy(animator, hintKeyword) : '';
        const attackClip =
          hinted ||
          findClipFuzzy(
            animator,
            'attack',
            'swing',
            'punch',
            'slash',
            'hit',
            'melee',
            'strike'
          );
        // Advance the combo pool only when the hinted clip actually played —
        // falling back to a generic swing must not burn a combo step.
        advancePlayerAttackCombo(
          !!hinted && Array.isArray(attackClipHint) && attackClipHint.length > 1
        );
        let clipDur = 0;
        if (attackClip) {
          const action = animator.playOverride(attackClip, {
            loop: false,
            timeScale: attackTimeScale,
            onFinished: () => {
              swingActive = false;
            },
          });
          // The trail follows *swings*, not any override: a roll, a gather or
          // an emote also lock locomotion, and a ribbon on those reads as the
          // weapon firing off on its own.
          swingActive = !!action;
          clipDur = action?.getClip()?.duration ?? 0;
        }
        // Schedule the blow for the impact frame instead of landing it now —
        // always, even when the rig has no attack clip. The clip plays at
        // `attackTimeScale`, so the wall-clock delay shrinks by the same rate.
        pendingMelee.set(
          eid,
          clipDur > 0
            ? (clipDur * ATTACK_IMPACT_FRACTION) / attackTimeScale
            : ATTACK_IMPACT_FALLBACK
        );
      }

      // Interact (F): only the bend-down "gather" when the nearest F-target
      // opted in (`gesture: 'gather'` — mushroom / ground loot). Portals,
      // chests, readables, etc. stay on locomotion (no long crouch).
      const interact = isKeyDown('KeyF') ? 1 : 0;
      const wasInteract = prevInteract.get(eid) ?? 0;
      prevInteract.set(eid, interact);
      if (interact && !wasInteract && grounded && !animator.overrideLock) {
        const nearest = findNearestInteractionTarget(
          state,
          Transform.posX[eid],
          Transform.posZ[eid],
          { key: 'F' }
        );
        if (resolveInteractionGesture(nearest?.info) === 'gather') {
          const gatherClip = findClipFuzzy(animator, 'gather');
          if (gatherClip) {
            animator.playOverride(gatherClip, {
              loop: false,
              timeScale: GATHER_TIME_SCALE,
            });
          }
        }
      }

      // Land the scheduled melee hit when the swing reaches its impact frame.
      const meleeWait = pendingMelee.get(eid);
      if (meleeWait !== undefined) {
        const left = meleeWait - dt;
        if (left <= 0) {
          meleeHit(state, eid);
          pendingMelee.delete(eid);
        } else {
          pendingMelee.set(eid, left);
        }
      }

      if (PlayerGltfConfig.overrideLock[eid] === 1 || animator.overrideLock) {
        animator.setAdditive('', 0); // no turn-lean during an attack override
        animator.update(dt);
        if (state.hasComponent(eid, WorldTransform)) {
          syncTransformToRoot(eid, animator, state, dt);
        }
        continue;
      }

      const loco = resolveLocomotion(state, animator, eid);
      if (loco.idle && loco.walk && loco.run) {
        const sig = `${loco.idle}|${loco.walk}|${loco.run}|${loco.jump ?? ''}`;
        if (prevLocoSig.get(eid) !== sig) {
          prevLocoSig.set(eid, sig);
          animator.registerLocomotionSet(DEFAULT_LOCOMOTION_SET, {
            idle: loco.idle,
            walk: loco.walk,
            run: loco.run,
            jump: loco.jump || undefined,
          });
        }
      }

      // A/D steers the camera AND pushes the character sideways (arc turn), so
      // both axes translate and drive the gait; steering additionally blends a
      // turn-lean clip on top.
      const moveX = InputState.moveX[eid];
      const moveY = InputState.moveY[eid];
      const translating = Math.abs(moveY) > 0.01 || Math.abs(moveX) > 0.01;
      const turning = Math.abs(moveX) > 0.01;
      const run = translating && isRunModifier();
      const airborne = !grounded && (loco.jump || loco.fall);

      // --- Base locomotion layer ---
      // Airborne uses jump (ascending) / fall (descending); grounded uses gait.
      if (airborne) {
        const clip = vy > 0.5 ? loco.jump || loco.fall : loco.fall || loco.jump;
        if (clip && animator.activeClipName !== clip) animator.play(clip);
      } else if (translating) {
        let clip = run ? loco.run : loco.walk;
        if (moveY < 0 && loco.back) clip = loco.back; // walking backward
        // phaseSync: gait cuts blend footfall-to-footfall (no foot slide/pop).
        if (clip && animator.activeClipName !== clip) {
          animator.play(clip, { phaseSync: true });
        }
      } else {
        // Idle — use a combat-guard idle when a weapon is equipped (idleClipHint),
        // falling back to the default relaxed idle.
        const idleClip =
          (idleClipHint && findClip(animator, idleClipHint)) || loco.idle;
        if (idleClip && animator.activeClipName !== idleClip) {
          animator.play(idleClip, { phaseSync: true });
        }
      }

      // Match gait cadence to the actual horizontal speed: the walk/run clips
      // are authored at a natural stride speed, but the controller moves much
      // faster — at timeScale 1 the feet glide and the gait reads as idle.
      if (translating && !airborne) {
        const planar = Math.hypot(
          CharacterMovement.desiredVelX[eid] || 0,
          CharacterMovement.desiredVelZ[eid] || 0
        );
        const ref = run ? RUN_CLIP_SPEED : WALK_CLIP_SPEED;
        animator.setTimeScale(Math.min(2.6, Math.max(0.6, planar / ref)));
      } else {
        animator.setTimeScale(1);
      }

      // --- Additive turn-lean overlay ---
      // Steering (A/D) blends a turn clip ON TOP of the base, so curving while
      // walking forward (W+D), or pivoting in place, both show the turn. moveX>0
      // (D) steers right → turn-right clip.
      if (turning && !airborne) {
        const turnClip = moveX > 0 ? loco.turnRight : loco.turnLeft;
        animator.setAdditive(turnClip, Math.min(1, Math.abs(moveX)));
      } else {
        animator.setAdditive('', 0);
      }

      animator.update(dt);

      if (!state.hasComponent(eid, WorldTransform)) {
        continue;
      }

      syncTransformToRoot(eid, animator, state, dt);
    }
  },
});

function syncTransformToRoot(
  eid: number,
  animator: GltfAnimator,
  state: State,
  dt: number
): void {
  const yOff = getYOffset(state, eid);
  const root = animator.root;
  root.position.set(
    WorldTransform.posX[eid],
    WorldTransform.posY[eid] + yOff,
    WorldTransform.posZ[eid]
  );
  // Exponential slerp toward the physics heading so the visible character
  // sweeps through turns instead of stepping with the fixed-tick rotation.
  // When aiming (face target set), yaw toward that world point instead.
  if (faceTargetX !== null) {
    const yaw = Math.atan2(
      faceTargetX - WorldTransform.posX[eid],
      faceTargetZ - WorldTransform.posZ[eid]
    );
    _visualQuat.setFromAxisAngle(_yAxis, yaw);
  } else {
    _visualQuat.set(
      WorldTransform.rotX[eid],
      WorldTransform.rotY[eid],
      WorldTransform.rotZ[eid],
      WorldTransform.rotW[eid]
    );
  }
  // Frame-rate-independent quaternion damping toward the facing target.
  // Replaces the manual `slerp(q, 1 - exp(-k*dt))` form; smoothTime ≈ 1/k
  // (VISUAL_TURN_RATE=10 → ~0.1s to settle).
  dampQ(root.quaternion, _visualQuat, 1 / VISUAL_TURN_RATE, dt);

  const debugCapsule = ensureDebugCapsule(state);
  if (debugCapsule) {
    const colliderOffY = state.hasComponent(eid, Collider)
      ? Collider.posOffsetY[eid]
      : PLAYER_COLLIDER_DEFAULTS.posOffsetY;
    debugCapsule.position.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid] + colliderOffY,
      WorldTransform.posZ[eid]
    );
    debugCapsule.quaternion.copy(root.quaternion);
  }
}
