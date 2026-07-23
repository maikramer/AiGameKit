import type { State } from '../../core';
import { Transform } from '../transforms/components';
import { Health } from '../combat/components';
import { YukaAgentComponent } from './components';
import {
  YUKA_BEHAVIOR_ARRIVE,
  YUKA_BEHAVIOR_EVADE,
  YUKA_BEHAVIOR_FLEE,
  YUKA_BEHAVIOR_PURSUIT,
  YUKA_BEHAVIOR_SEPARATION,
  YUKA_BEHAVIOR_FLOCK,
  YUKA_BEHAVIOR_WANDER,
  YUKA_BEHAVIOR_HOLD_RING,
} from './components';

/**
 * A creature "personality" — tunable knobs the decision layer reads each frame
 * to pick the steering mask + focus target. This is a light utility-AI (score
 * the situation, choose the highest-scoring behavior) rather than yuka's full
 * `Think`/`GoalEvaluator` machinery, which is verbose to author and hard to
 * tweak live in the browser. The knobs map 1:1 onto the behaviors you want to
 * feel: when a wolf should back off, when a goblin should dodge, when a caster
 * should flee to range.
 */
export interface CreatureDecisionProfile {
  /** Below this HP fraction, flee toward maxRange instead of pressing in. */
  fleeBelowHpFrac?: number;
  /** Preferred stand-off distance from the focus target (m). 0 = body-block. */
  standOffRange?: number;
  /** Distance at which the creature stops fleeing and re-engages (m). */
  reengageRange?: number;
  /** If true, kite (evade while firing) when the focus target is close. */
  kite?: boolean;
  /** If true, apply separation (don't stack on allies). */
  separate?: boolean;
  /** If true, flock with allies (alignment + cohesion + separation). */
  flock?: boolean;
}

export interface DecisionInput {
  state: State;
  eid: number;
  targetEid: number;
  profile: CreatureDecisionProfile;
}

export interface DecisionResult {
  /** OR of YUKA_BEHAVIOR_* flags to activate this frame. */
  mask: number;
  /** Focus target eid (0 = use static target / wander). */
  targetEid: number;
}

/**
 * Score the situation for a creature and return the steering mask + target.
 * Pure function over the ECS arrays — no allocation, safe to call every frame
 * for every awake creature.
 *
 * The priority order is deliberate and readable:
 *   1. No target → wander + flock/separate (ambient pack behavior).
 *   2. HP below flee threshold → flee + separate (survive).
 *   3. Target closer than stand-off → evade/kite back to range (casters, wolves).
 *   4. Target beyond stand-off → pursue + flock (close the gap as a pack).
 *   5. At stand-off → arrive (decelerate into the ring) + separate.
 */
export function decide(input: DecisionInput): DecisionResult {
  const { state, eid, targetEid, profile } = input;
  const packFlags =
    (profile.flock ? YUKA_BEHAVIOR_FLOCK : 0) |
    (profile.separate ? YUKA_BEHAVIOR_SEPARATION : 0);

  if (targetEid <= 0 || Health.current[targetEid] <= 0) {
    return { mask: YUKA_BEHAVIOR_WANDER | packFlags, targetEid: 0 };
  }

  const hpFrac = hpFraction(eid);
  if (
    profile.fleeBelowHpFrac !== undefined &&
    hpFrac < profile.fleeBelowHpFrac
  ) {
    return { mask: YUKA_BEHAVIOR_FLEE | packFlags, targetEid };
  }

  const dist = planarDistance(state, eid, targetEid);
  const standOff = profile.standOffRange ?? 0;

  // Too close — back off (kite / hit-and-run).
  if (standOff > 0 && dist < standOff * 0.8) {
    const back = profile.kite ? YUKA_BEHAVIOR_EVADE : YUKA_BEHAVIOR_FLEE;
    return { mask: back | packFlags, targetEid };
  }

  // Too far — close in as a pack.
  if (dist > standOff * 1.2) {
    return {
      mask: YUKA_BEHAVIOR_PURSUIT | packFlags,
      targetEid,
    };
  }

  // In the band — hold position, decelerate, keep spacing.
  return {
    mask: YUKA_BEHAVIOR_ARRIVE | YUKA_BEHAVIOR_HOLD_RING | packFlags,
    targetEid,
  };
}

function hpFraction(eid: number): number {
  const max = Health.max[eid] || 1;
  return Health.current[eid] / max;
}

function planarDistance(state: State, a: number, b: number): number {
  const dx = Transform.posX[a] - Transform.posX[b];
  const dz = Transform.posZ[a] - Transform.posZ[b];
  void state;
  return Math.sqrt(dx * dx + dz * dz);
}

/**
 * Push a decision result into the SoA component so {@link YukaAgentSystem}
 * picks it up. Callers (creature scripts) call this once per frame after
 * {@link decide}.
 */
export function applyDecision(
  state: State,
  eid: number,
  result: DecisionResult
): void {
  void state;
  YukaAgentComponent.behavior[eid] = result.mask;
  YukaAgentComponent.targetEid[eid] = result.targetEid;
  YukaAgentComponent.active[eid] = 1;
}
