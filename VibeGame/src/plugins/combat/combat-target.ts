import type { State } from '../../core';
import { Health } from './components';

/**
 * Soft-lock combat target — the enemy the HUD TargetBar tracks and that
 * melee/aim helpers prefer. Refreshed on hit / aggro; cleared on death or TTL.
 */

const DEFAULT_TTL = 5;

let targetEid = -1;
let targetLabel = '';
let remainingTtl = 0;

export interface SetCombatTargetOptions {
  /** Display name for the TargetBar (falls back to entity name / "Enemy"). */
  label?: string;
  /** Seconds to keep the lock without refresh (default 5). */
  ttl?: number;
}

/** Soft-lock `eid` as the current combat target. */
export function setCombatTarget(
  eid: number,
  options?: SetCombatTargetOptions
): void {
  if (eid <= 0) {
    clearCombatTarget();
    return;
  }
  targetEid = eid;
  targetLabel = options?.label?.trim() ?? targetLabel;
  remainingTtl = options?.ttl ?? DEFAULT_TTL;
}

/** Current soft-locked entity id, or -1 when none. */
export function getCombatTarget(): number {
  return targetEid;
}

/** Label for the TargetBar (may be empty until a hit sets one). */
export function getCombatTargetLabel(): string {
  return targetLabel;
}

export function clearCombatTarget(): void {
  targetEid = -1;
  targetLabel = '';
  remainingTtl = 0;
}

/**
 * Decay TTL and clear when the target is dead / gone. Call once per frame
 * from a simulation system (game CombatFeedback or engine CombatPlugin).
 */
export function tickCombatTarget(state: State, dt: number): void {
  if (targetEid < 0) return;
  remainingTtl -= dt;
  if (
    remainingTtl <= 0 ||
    !state.exists(targetEid) ||
    !state.hasComponent(targetEid, Health) ||
    Health.current[targetEid] <= 0
  ) {
    clearCombatTarget();
  }
}
