import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/** Biome type stored on {@link BiomeRegion}.type: 0=vale, 1=floresta, 2=deserto, 3=pântano, 4=montanha. */
export const BIOME_TYPE_VALE = 0;
export const BIOME_TYPE_FLORESTA = 1;
export const BIOME_TYPE_DESERTO = 2;
export const BIOME_TYPE_PANTANO = 3;
export const BIOME_TYPE_MONTANHA = 4;

/**
 * A polygonal world region that applies fog/ambient/tint/BGM overrides while
 * the player is inside it. One entity per `<BiomeRegion>` element. Only the
 * AABB lives here; the polygon vertices are variable-length and kept in the
 * parser WeakMap (`parser.ts`) for narrow-phase point-in-polygon tests.
 */
export const BiomeRegion = defineComponent({
  polyMinX: F32,
  polyMinZ: F32,
  polyMaxX: F32,
  polyMaxZ: F32,
  type: U8,
  tintR: F32,
  tintG: F32,
  tintB: F32,
  // Packed 0xRRGGBB (matches the Postprocessing.fogColor convention).
  fogColor: U32,
  fogDensity: F32,
  ambientR: F32,
  ambientG: F32,
  ambientB: F32,
  bgmLayer: U8,
  // Per-biome post-processing overrides (0 = inherit the scene baseline).
  ppExposure: F32,
  ppBloomStrength: F32,
  ppVignetteDarkness: F32,
  /** Rain intensity 0..1 while the player is inside this biome. */
  rain: F32,
  /**
   * Cloud coverage 0..1 while inside this biome.
   * Sentinel `-1` = no override (keep Weather / cycle baseline).
   */
  clouds: F32,
});

/**
 * Per-player biome blend state. `current`/`target` hold BiomeRegion entity ids
 * or {@link NO_BIOME} (default vale); `blend` is the 0..1 crossfade progress.
 */
export const ActiveBiome = defineComponent({
  current: U32,
  target: U32,
  blend: F32,
});
