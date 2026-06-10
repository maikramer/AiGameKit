import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * World-space floating text (damage numbers, "+1 item" pickups…). The string
 * itself lives in a sidecar map (see utils.ts) — SOA fields are numeric.
 */
export const FloatingText = {
  elapsed: new Float32Array(MAX_ENTITIES),
  /** Lifetime in seconds; the entity is destroyed when elapsed reaches it. */
  duration: new Float32Array(MAX_ENTITIES),
  /** Upward drift in m/s. */
  riseSpeed: new Float32Array(MAX_ENTITIES),
  /** Font size in world meters. */
  size: new Float32Array(MAX_ENTITIES),
  colorR: new Float32Array(MAX_ENTITIES),
  colorG: new Float32Array(MAX_ENTITIES),
  colorB: new Float32Array(MAX_ENTITIES),
} as const;
