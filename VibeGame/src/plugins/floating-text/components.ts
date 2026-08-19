import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

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
export const FloatingText = defineComponent({
  elapsed: F32,
  /** Lifetime in seconds; the entity is destroyed when elapsed reaches it. */
  duration: F32,
  /** Upward drift. World mode: m/s. Screen mode: px/s. */
  riseSpeed: F32,
  /** Font size in world meters (world mode). */
  size: F32,
  colorR: F32,
  colorG: F32,
  colorB: F32,

  /** 0 = world (troika 3D), 1 = screen (DOM pool). */
  space: U8,
  /** Initial screen-space X in CSS pixels (screen mode). */
  screenX: F32,
  /** Initial screen-space Y in CSS pixels (screen mode). */
  screenY: F32,
  /** Font size in CSS pixels (screen mode). */
  fontSizePx: F32,
  /** Horizontal drift in CSS pixels (screen mode); signed. */
  driftX: F32,
  /** Crit flag (screen mode): bigger font + red-orange tint override. */
  crit: U8,
});
