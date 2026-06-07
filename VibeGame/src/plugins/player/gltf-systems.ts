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
const prevPrimary = new Map<number, number>();

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
        const attackClip = findClipFuzzy(animator, 'attack');
        if (attackClip) animator.playOverride(attackClip, { loop: false });
        meleeHit(state, eid);
      }

      if (PlayerGltfConfig.overrideLock[eid] === 1 || animator.overrideLock) {
        animator.setAdditive('', 0); // no turn-lean during an attack override
        animator.update(dt);
        if (state.hasComponent(eid, WorldTransform)) {
          syncTransformToRoot(eid, animator, state);
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

      // Separate translation (W/S) from steering (A/D). In third-person the
      // heading is steered by A/D, so |moveX| alone is a turn-in-place, not a
      // walk — keying `moving` off moveX made the hero walk-in-place while
      // spinning. Translation drives the gait; steering plays a turn clip.
      const moveX = InputState.moveX[eid];
      const moveY = InputState.moveY[eid];
      const translating = Math.abs(moveY) > 0.01;
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
      animator.setTimeScale(1);

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

      syncTransformToRoot(eid, animator, state);
    }
  },
};

function syncTransformToRoot(
  eid: number,
  animator: GltfAnimator,
  state: State
): void {
  const yOff = getYOffset(state, eid);
  const root = animator.root;
  root.position.set(
    WorldTransform.posX[eid],
    WorldTransform.posY[eid] + yOff,
    WorldTransform.posZ[eid]
  );
  root.quaternion.set(
    WorldTransform.rotX[eid],
    WorldTransform.rotY[eid],
    WorldTransform.rotZ[eid],
    WorldTransform.rotW[eid]
  );

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
