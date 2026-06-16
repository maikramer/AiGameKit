import { Box3, LineSegments, Quaternion, Vector3 } from 'three';
import { defineQuery, type Adapter, type State, type System } from '../../core';
import { loadGltfAnimated } from '../../extras/gltf-bridge';
import { GltfAnimator } from '../../extras/gltf-animator';
import { animatorRegistry, registerAnimator } from '../gltf-anim/systems';
import { HasAnimator } from '../animation/components';
import { InputState } from '../input/components';
import { isKeyDown } from '../input/utils';
import { CharacterController, CharacterMovement } from '../physics';
import { Transform, WorldTransform } from '../transforms';
import { PlayerController, PlayerGltfConfig } from './components';
import { PLAYER_COLLIDER_DEFAULTS } from './constants';

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

const ATTACK_RANGE = 2.2; // m
const ATTACK_DAMAGE = 25;
// Fraction of the attack clip after which the blow lands — the hit registers
// near the end of the swing (matching the visual impact) instead of the instant
// the button is pressed. Mirrors Destructible.impactFraction for props.
const ATTACK_IMPACT_FRACTION = 0.7;
// Fallback impact delay when the clip duration is unknown.
const ATTACK_IMPACT_FALLBACK = 0.4; // s
const prevPrimary = new Map<number, number>();
// Per-attacker countdown until the pending melee blow lands (seconds).
const pendingMelee = new Map<number, number>();

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

/** First clip whose name contains any of the keywords (case-insensitive). */
function findClip(animator: GltfAnimator, ...keywords: string[]): string {
  for (const k of keywords) {
    const hit = animator.clipNames.find((n) => n.toLowerCase().includes(k));
    if (hit) return hit;
  }
  return '';
}

/** Fuzzy clip search: tries progressively relaxed matching strategies. */
function findClipFuzzy(animator: GltfAnimator, ...keywords: string[]): string {
  // Strategy 1: exact keyword containment (original)
  const direct = findClip(animator, ...keywords);
  if (direct) return direct;

  const lower = animator.clipNames.map((n) => n.toLowerCase());

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
      if (idx >= 0) return animator.clipNames[idx];
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

/** Resolve clips by explicit index override (>0) else by name keyword (fuzzy). */
function resolveLocomotion(animator: GltfAnimator, eid: number): Locomotion {
  const names = animator.clipNames;
  const byIndex = (field: number): string =>
    field > 0 && field < names.length ? names[field] : '';
  return {
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
}

function isRunModifier(): boolean {
  return isKeyDown('ShiftLeft') || isKeyDown('ShiftRight');
}

let meleeQuery: ReturnType<typeof defineQuery> | null = null;

/** Damage Health entities within a forward cone when an attack lands. */
function meleeHit(state: State, attacker: number): void {
  const HealthComp = state.getComponent('health');
  if (!HealthComp || !state.hasComponent(attacker, WorldTransform)) return;
  const Health = HealthComp as unknown as { current: Float32Array };
  if (!meleeQuery) meleeQuery = defineQuery([HealthComp, Transform]);

  const ax = WorldTransform.posX[attacker];
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
    const dx = Transform.posX[target] - ax;
    const dz = Transform.posZ[target] - az;
    const dist = Math.hypot(dx, dz);
    if (dist > ATTACK_RANGE || dist < 0.001) continue;
    // in front (within ~70°)
    if ((dx * _fwd.x + dz * _fwd.z) / dist < 0.35) continue;
    Health.current[target] = Math.max(
      0,
      Health.current[target] - ATTACK_DAMAGE
    );
  }
}

const playerGltfSetupQuery = defineQuery([PlayerController, PlayerGltfConfig]);

/** Runs in the first setup bucket so {@link HasAnimator} exists before the procedural character is spawned. */
export const PlayerGltfEnsureHasAnimatorSystem: System = {
  group: 'setup',
  first: true,
  update: (state) => {
    for (const eid of playerGltfSetupQuery(state.world)) {
      if (!state.hasComponent(eid, HasAnimator)) {
        state.addComponent(eid, HasAnimator);
      }
    }
  },
};

export const PlayerGltfSetupSystem: System = {
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
      void loadGltfAnimated(state, url)
        .then((gltf) => {
          const box = new Box3().setFromObject(gltf.scene);
          const yOffset = Number.isFinite(box.min.y) ? -box.min.y : 0;
          setYOffset(state, eid, yOffset);

          const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });
          const regIdx = registerAnimator(animator);
          PlayerGltfConfig.animatorRegistryIndex[eid] = regIdx;

          const loco = resolveLocomotion(animator, eid);
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
          console.error('[player-gltf] load failed', err);
        })
        .finally(() => {
          PlayerGltfConfig.loaded[eid] = 1;
          setLoadInFlight(state, eid, false);
        });
    }
  },
};

const playerGltfAnimQuery = defineQuery([
  PlayerController,
  PlayerGltfConfig,
  InputState,
]);

function ensureDebugCapsule(_state: State): LineSegments | null {
  return null;
}

export const PlayerGltfAnimStateSystem: System = {
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

      const animator = animatorRegistry.get(regIdx);
      if (!animator) {
        continue;
      }

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
        // Play the swing clip if the rig has one (don't gate the hit on it).
        const attackClip = findClipFuzzy(
          animator,
          'attack',
          'swing',
          'punch',
          'slash',
          'hit',
          'melee',
          'strike'
        );
        let clipDur = 0;
        if (attackClip) {
          const action = animator.playOverride(attackClip, { loop: false });
          clipDur = action?.getClip()?.duration ?? 0;
        }
        // Schedule the blow for the impact frame instead of landing it now —
        // always, even when the rig has no attack clip.
        pendingMelee.set(
          eid,
          clipDur > 0 ? clipDur * ATTACK_IMPACT_FRACTION : ATTACK_IMPACT_FALLBACK
        );
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

      const loco = resolveLocomotion(animator, eid);
      if (loco.idle && loco.walk && loco.run) {
        animator.registerLocomotionSet(DEFAULT_LOCOMOTION_SET, {
          idle: loco.idle,
          walk: loco.walk,
          run: loco.run,
          jump: loco.jump || undefined,
        });
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
        if (clip && animator.activeClipName !== clip) animator.play(clip);
      } else if (loco.idle && animator.activeClipName !== loco.idle) {
        animator.play(loco.idle);
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
};

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
  _visualQuat.set(
    WorldTransform.rotX[eid],
    WorldTransform.rotY[eid],
    WorldTransform.rotZ[eid],
    WorldTransform.rotW[eid]
  );
  root.quaternion.slerp(_visualQuat, 1 - Math.exp(-VISUAL_TURN_RATE * dt));

  const debugCapsule = ensureDebugCapsule(state);
  if (debugCapsule) {
    debugCapsule.position.set(
      WorldTransform.posX[eid],
      WorldTransform.posY[eid] + PLAYER_COLLIDER_DEFAULTS.posOffsetY,
      WorldTransform.posZ[eid]
    );
    debugCapsule.quaternion.copy(root.quaternion);
  }
}
