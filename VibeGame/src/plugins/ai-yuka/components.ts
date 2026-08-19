import {
  defineComponent,
  filled,
  F32,
  U8,
  U32,
} from '../../core/ecs/component-storage';

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
 * terrain snap, never by the steerer.
 */
export const YukaAgentComponent = defineComponent({
  /** When 0 the system skips this entity entirely (sleeping / dead). */
  active: U8,
  /** OR of {@link YukaBehaviorMask} flags. */
  behavior: U32,
  /** Max planar movement speed (m/s). Passed to the yuka Vehicle each frame. */
  maxSpeed: F32,
  /** Max steering force; higher = snappier turns. */
  maxForce: filled(F32, 8),
  /** Focus entity (hero). 0 = use static target. */
  targetEid: U32,
  /** Faction id (so separation/flock only apply to allies, not enemies). */
  faction: U8,
  /** Static target X (used when targetEid === 0, e.g. wander anchor). */
  targetX: F32,
  /** Static target Z. */
  targetZ: F32,
});
