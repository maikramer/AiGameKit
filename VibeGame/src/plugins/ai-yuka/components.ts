import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Behavior flags for a yuka-driven agent. They are **bitmask-combinable** so a
 * creature can run e.g. `PURSUIT | SEPARATION` (chase while pushing away from
 * pack-mates) without a dedicated mode enum per combination. The
 * {@link YukaAgentSystem} maps each set bit to one or more yuka steering
 * behaviors on the entity's {@link Vehicle}.
 *
 * Values chosen as powers of two so callers can OR them.
 */
export const YUKA_BEHAVIOR_NONE = 0;
export const YUKA_BEHAVIOR_SEEK = 1 << 0;
export const YUKA_BEHAVIOR_ARRIVE = 1 << 1;
export const YUKA_BEHAVIOR_PURSUIT = 1 << 2;
export const YUKA_BEHAVIOR_EVADE = 1 << 3;
export const YUKA_BEHAVIOR_FLEE = 1 << 4;
export const YUKA_BEHAVIOR_WANDER = 1 << 5;
/** Shove away from nearby allies so a pack does not stack on one spot. */
export const YUKA_BEHAVIOR_SEPARATION = 1 << 6;
/** Flock with allies of the same faction (alignment + cohesion + separation). */
export const YUKA_BEHAVIOR_FLOCK = 1 << 7;
/** Hold a standoff distance: decelerate into a ring around the target. */
export const YUKA_BEHAVIOR_HOLD_RING = 1 << 8;

export type YukaBehaviorMask = number;

/**
 * Queryable, SoA AI state for yuka-driven agents. The rich {@link Vehicle} and
 * its steering behaviors live in a per-`State` side table (see `context.ts`),
 * mirroring how `rpg-ai` keeps its config/instance scratch off the ECS arrays.
 *
 * `targetEid` is the *focus* entity (usually the hero); `targetX/Z` is the
 * static fallback used when `targetEid` is 0. Both are planar — Y is owned by
 * terrain snap, never by the steerer (same contract as `ai-steering`).
 */
export const YukaAgentComponent = {
  /** When 0 the system skips this entity entirely (sleeping / dead). */
  active: new Uint8Array(MAX_ENTITIES),
  /** OR of {@link YukaBehaviorMask} flags. */
  behavior: new Uint32Array(MAX_ENTITIES),
  /** Max planar movement speed (m/s). Passed to the yuka Vehicle each frame. */
  maxSpeed: new Float32Array(MAX_ENTITIES),
  /** Max steering force; higher = snappier turns. */
  maxForce: new Float32Array(MAX_ENTITIES).fill(8),
  /** Focus entity (hero). 0 = use static target. */
  targetEid: new Uint32Array(MAX_ENTITIES),
  /** Faction id (so separation/flock only apply to allies, not enemies). */
  faction: new Uint8Array(MAX_ENTITIES),
  /** Static target X (used when targetEid === 0, e.g. wander anchor). */
  targetX: new Float32Array(MAX_ENTITIES),
  /** Static target Z. */
  targetZ: new Float32Array(MAX_ENTITIES),
} as const;
