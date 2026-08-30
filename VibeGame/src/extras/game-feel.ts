// Combat "feel" primitives shared by games built on the engine: impact freeze
// frames (hit-stop) and pushback for character-controller bodies (CCT
// knockback). Games wire them from their own damage code — see
// examples/simple-rpg `src/game/melee.ts` for the reference usage.
import type { State } from '../core';
import { Transform } from '../plugins/transforms/components';
import { Rigidbody } from '../plugins/physics/components';
import { markRigidbodyPoseDirty } from '../plugins/physics/utils';
import { NavMeshAgent } from '../plugins/navmesh/components';

// ── Hit-stop ─────────────────────────────────────────────────────────────────
// A few frames of near-zero time scale on impact. Everything driven by scaled
// dt (FSM, physics accumulator, animators) freezes with the world; effects that
// must keep moving (camera shake) read unscaled time instead.

interface HitStopState {
  remaining: number;
  scale: number;
  timeScaleBefore: number;
}

const hitStopByState = new WeakMap<State, HitStopState>();

/**
 * Freeze the world for `durationSec` at `timeScale` (0..1, default 0.05).
 * Overlapping requests keep the strongest freeze and the longest window.
 * Pair with {@link tickHitStop} once per frame (unscaled dt) to restore.
 */
export function hitStop(
  state: State,
  durationSec: number,
  timeScale = 0.05
): void {
  if (durationSec <= 0 || timeScale >= 1) return;
  const active = hitStopByState.get(state);
  if (active && active.remaining > 0) {
    active.remaining = Math.max(active.remaining, durationSec);
    active.scale = Math.min(active.scale, timeScale);
    state.time.timeScale = active.scale;
    return;
  }
  hitStopByState.set(state, {
    remaining: durationSec,
    scale: timeScale,
    timeScaleBefore: state.time.timeScale,
  });
  state.time.timeScale = timeScale;
}

/**
 * Advance/expire the active hit-stop. Call once per frame with **unscaled**
 * dt — scaled dt is ~0 during the freeze, so the stop would never end.
 *
 * Run this in a `late` system ordered after the pause coordinator: the pause
 * system re-asserts its own timeScale contract every frame, which would
 * otherwise wipe the freeze before the scheduler's next tick sees it — so an
 * active stop re-applies its scale here (unless the game is hard-paused at
 * timeScale 0, in which case the stop holds until unpause).
 */
export function tickHitStop(state: State, unscaledDt: number): void {
  const active = hitStopByState.get(state);
  if (!active || active.remaining <= 0) return;
  if (state.time.timeScale === 0) return; // paused: the pause contract wins
  if (state.time.timeScale !== active.scale) {
    state.time.timeScale = active.scale;
  }
  active.remaining -= unscaledDt;
  if (active.remaining <= 0) {
    state.time.timeScale = active.timeScaleBefore;
    hitStopByState.delete(state);
  }
}

export function hitStopActive(state: State): boolean {
  return (hitStopByState.get(state)?.remaining ?? 0) > 0;
}

// ── CCT knockback ────────────────────────────────────────────────────────────
// Pushback for creatures/characters moved by kinematic CCT: physics owns
// Transform after each fixed step, so the shove writes Rigidbody too (the same
// contract as the melee-AI lunge dash). Navmesh steering is suspended while the
// shove runs so it cannot fight the displacement.

interface Knockback {
  originX: number;
  originZ: number;
  dirX: number;
  dirZ: number;
  distance: number;
  duration: number;
  elapsed: number;
}

const knockbackByState = new WeakMap<State, Map<number, Knockback>>();

/** Ease-out quad: the shove starts fast and settles — reads as weight. */
function knockbackEase(f: number): number {
  return 1 - (1 - f) * (1 - f);
}

/**
 * Shove `eid` along (`dirX`, `dirZ`) — planar, normalized here — covering
 * `distance` meters over `duration` seconds. Returns false when the direction
 * is degenerate. The displacement is absolute (origin + eased distance), so
 * callers must also stagger/pause the entity's own steering for the duration.
 */
export function applyCctKnockback(
  state: State,
  eid: number,
  dirX: number,
  dirZ: number,
  distance = 0.8,
  duration = 0.18
): boolean {
  if (distance <= 0 || duration <= 0) return false;
  const len = Math.hypot(dirX, dirZ);
  if (len < 1e-4) return false;
  let m = knockbackByState.get(state);
  if (!m) {
    m = new Map();
    knockbackByState.set(state, m);
  }
  m.set(eid, {
    originX: Transform.posX[eid],
    originZ: Transform.posZ[eid],
    dirX: dirX / len,
    dirZ: dirZ / len,
    distance,
    duration,
    elapsed: 0,
  });
  if (state.hasComponent(eid, NavMeshAgent)) {
    NavMeshAgent.suspended[eid] = 1;
  }
  return true;
}

export function isCctKnockbackActive(state: State, eid: number): boolean {
  return knockbackByState.get(state)?.has(eid) ?? false;
}

/**
 * Advance all active knockbacks one frame. Call once per frame with the
 * **scaled** dt so shoves freeze together with the world during hit-stop
 * (freeze → slow-motion pushback is the intended read).
 */
export function tickCctKnockbacks(state: State, dt: number): void {
  const m = knockbackByState.get(state);
  if (!m || m.size === 0) return;
  for (const [eid, k] of m) {
    if (typeof state.exists === 'function' && !state.exists(eid)) {
      m.delete(eid);
      continue;
    }
    k.elapsed += dt;
    const f = Math.min(1, k.elapsed / k.duration);
    const travelled = k.distance * knockbackEase(f);
    const nx = k.originX + k.dirX * travelled;
    const nz = k.originZ + k.dirZ * travelled;
    Transform.posX[eid] = nx;
    Transform.posZ[eid] = nz;
    Transform.dirty[eid] = 1;
    if (state.hasComponent(eid, Rigidbody)) {
      Rigidbody.posX[eid] = nx;
      Rigidbody.posZ[eid] = nz;
      markRigidbodyPoseDirty(eid);
    }
    if (f >= 1) {
      m.delete(eid);
      if (state.hasComponent(eid, NavMeshAgent)) {
        NavMeshAgent.suspended[eid] = 0;
      }
    }
  }
}

/** HMR/teardown: release suspended agents and drop every active shove. */
export function clearCctKnockbacks(state: State): void {
  const m = knockbackByState.get(state);
  if (!m) return;
  for (const eid of m.keys()) {
    if (state.hasComponent(eid, NavMeshAgent)) {
      NavMeshAgent.suspended[eid] = 0;
    }
  }
  m.clear();
}
