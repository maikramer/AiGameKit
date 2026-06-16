import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Floating text — supports two rendering modes that share the same SOA data:
 *
 *  - `space === 0` (world, default): troika-three-text SDF glyphs in the 3D
 *    scene, billboarded to the active camera. Uses `riseSpeed` (m/s) and
 *    `size` (world meters).
 *  - `space === 1` (screen): DOM `<span class="vibe-float-screen">` recycled
 *    through a pool and mounted in the HudScreenLayer. Uses `screenX/Y` (px),
 *    `fontSizePx`, `driftX` (px) and `crit` (bigger/hotter variant).
 *
 * The string payload itself lives in a sidecar map (utils.ts) — SOA fields
 * stay numeric. Color R/G/B floats are shared between both modes.
 */
export const FloatingText = {
  elapsed: new Float32Array(MAX_ENTITIES),
  /** Lifetime in seconds; the entity is destroyed when elapsed reaches it. */
  duration: new Float32Array(MAX_ENTITIES),
  /** Upward drift. World mode: m/s. Screen mode: px/s. */
  riseSpeed: new Float32Array(MAX_ENTITIES),
  /** Font size in world meters (world mode). */
  size: new Float32Array(MAX_ENTITIES),
  colorR: new Float32Array(MAX_ENTITIES),
  colorG: new Float32Array(MAX_ENTITIES),
  colorB: new Float32Array(MAX_ENTITIES),

  /** 0 = world (troika 3D), 1 = screen (DOM pool). */
  space: new Uint8Array(MAX_ENTITIES),
  /** Initial screen-space X in CSS pixels (screen mode). */
  screenX: new Float32Array(MAX_ENTITIES),
  /** Initial screen-space Y in CSS pixels (screen mode). */
  screenY: new Float32Array(MAX_ENTITIES),
  /** Font size in CSS pixels (screen mode). */
  fontSizePx: new Float32Array(MAX_ENTITIES),
  /** Horizontal drift in CSS pixels (screen mode); signed. */
  driftX: new Float32Array(MAX_ENTITIES),
  /** Crit flag (screen mode): bigger font + red-orange tint override. */
  crit: new Uint8Array(MAX_ENTITIES),
} as const;
