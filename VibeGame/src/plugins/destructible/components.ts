import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

/**
 * Player-breakable prop: swinging the primary attack within range commits a
 * hit that lands near the end of the attack clip; on the final hit the prop
 * bursts into particles, optionally pops a floating text, and is destroyed.
 */
export const Destructible = defineComponent({
  /** Swings needed to break (default 1). */
  hits: U8,
  hitsTaken: U8,
  /** Attack reach in meters (default 3.5). */
  range: F32,
  /** Fraction of the attack clip after which the blow lands (default 0.75). */
  impactFraction: F32,
  /** Countdown until the committed swing lands; 0 = idle. */
  pendingImpact: F32,
  /** Particle preset for the break burst (particle-emitter preset enum). */
  preset: U8,
  burstCount: F32,
  /** Snap the player's facing toward the prop when the swing starts. */
  faceOnHit: U8,
  /** Sparks feedback on non-final hits. */
  sparkOnHit: U8,
  /** Particle preset spawned on each non-final hit (default: sparks). */
  hitPreset: U8,
  hitBurstCount: F32,
  /** Break FX style: 0 = burst only, 1 = fall (felled tree), 2 = shatter (rock), 3 = split (tree halves). */
  breakStyle: U8,
  /** Darkening crack overlay that deepens with each hit (rocks). */
  crackOnHit: U8,
  /**
   * Crack overlay style: 0 = voronoi (rocks, default), 1 = vertical streaks
   * (wood). Picked once per entity on the first hit; the material clone is
   * cached for the entity's lifetime.
   */
  crackStyle: U8,
  /** Decaying wobble on the visual with each hit (trees). */
  shakeOnHit: U8,
  /** Trunk cut height in meters for `breakStyle: fall` (default 0.6). */
  cutHeight: F32,
  popupColorR: F32,
  popupColorG: F32,
  popupColorB: F32,
  popupSize: F32,
});
