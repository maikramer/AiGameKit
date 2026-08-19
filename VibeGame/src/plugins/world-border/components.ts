import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/**
 * Circular soft world border. Crossing `radius` starts a `warnSeconds`
 * countdown (screen-space numbers); when it expires the player is teleported
 * back to the nearest point inside the border, seated on the surface with
 * velocity zeroed. Returning inside during the countdown cancels silently.
 */
export const WorldBorder = defineComponent({
  /** Border radius in meters (circle around the world origin). */
  radius: F32,
  /** Seconds outside the border before the teleport. */
  warnSeconds: F32,
  /** How far inside the border the player lands after the teleport (m). */
  margin: F32,
  /** Countdown expiry (state.time.elapsed seconds); 0 = not warning. */
  warnUntil: F32,
  /** Last whole second shown, so the countdown ticks once per second. */
  lastShownSecond: U8,
  /** 1 while the entity is being teleported this frame (feedback latch). */
  teleported: U8,
});
