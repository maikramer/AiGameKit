import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Player-breakable prop: swinging the primary attack within range commits a
 * hit that lands near the end of the attack clip; on the final hit the prop
 * bursts into particles, optionally pops a floating text, and is destroyed.
 */
export const Destructible = {
  /** Swings needed to break (default 1). */
  hits: new Uint8Array(MAX_ENTITIES),
  hitsTaken: new Uint8Array(MAX_ENTITIES),
  /** Attack reach in meters (default 3.5). */
  range: new Float32Array(MAX_ENTITIES),
  /** Fraction of the attack clip after which the blow lands (default 0.75). */
  impactFraction: new Float32Array(MAX_ENTITIES),
  /** Countdown until the committed swing lands; 0 = idle. */
  pendingImpact: new Float32Array(MAX_ENTITIES),
  /** Particle preset for the break burst (particle-emitter preset enum). */
  preset: new Uint8Array(MAX_ENTITIES),
  burstCount: new Float32Array(MAX_ENTITIES),
  /** Snap the player's facing toward the prop when the swing starts. */
  faceOnHit: new Uint8Array(MAX_ENTITIES),
  /** Sparks feedback on non-final hits. */
  sparkOnHit: new Uint8Array(MAX_ENTITIES),
  /** Particle preset spawned on each non-final hit (default: sparks). */
  hitPreset: new Uint8Array(MAX_ENTITIES),
  hitBurstCount: new Float32Array(MAX_ENTITIES),
  /** Break FX style: 0 = burst only, 1 = fall (felled tree), 2 = shatter (rock), 3 = split (tree halves). */
  breakStyle: new Uint8Array(MAX_ENTITIES),
  /** Darkening crack overlay that deepens with each hit (rocks). */
  crackOnHit: new Uint8Array(MAX_ENTITIES),
  /**
   * Crack overlay style: 0 = voronoi (rocks, default), 1 = vertical streaks
   * (wood). Picked once per entity on the first hit; the material clone is
   * cached for the entity's lifetime.
   */
  crackStyle: new Uint8Array(MAX_ENTITIES),
  /** Decaying wobble on the visual with each hit (trees). */
  shakeOnHit: new Uint8Array(MAX_ENTITIES),
  /** Trunk cut height in meters for `breakStyle: fall` (default 0.6). */
  cutHeight: new Float32Array(MAX_ENTITIES),
  popupColorR: new Float32Array(MAX_ENTITIES),
  popupColorG: new Float32Array(MAX_ENTITIES),
  popupColorB: new Float32Array(MAX_ENTITIES),
  popupSize: new Float32Array(MAX_ENTITIES),
} as const;
