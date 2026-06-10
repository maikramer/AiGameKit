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
  popupColorR: new Float32Array(MAX_ENTITIES),
  popupColorG: new Float32Array(MAX_ENTITIES),
  popupColorB: new Float32Array(MAX_ENTITIES),
  popupSize: new Float32Array(MAX_ENTITIES),
} as const;
