import { MAX_ENTITIES } from '../../core/ecs/constants';

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
export const BiomeRegion = {
  polyMinX: new Float32Array(MAX_ENTITIES),
  polyMinZ: new Float32Array(MAX_ENTITIES),
  polyMaxX: new Float32Array(MAX_ENTITIES),
  polyMaxZ: new Float32Array(MAX_ENTITIES),
  type: new Uint8Array(MAX_ENTITIES),
  tintR: new Float32Array(MAX_ENTITIES),
  tintG: new Float32Array(MAX_ENTITIES),
  tintB: new Float32Array(MAX_ENTITIES),
  // Packed 0xRRGGBB (matches the Postprocessing.fogColor convention).
  fogColor: new Uint32Array(MAX_ENTITIES),
  fogDensity: new Float32Array(MAX_ENTITIES),
  ambientR: new Float32Array(MAX_ENTITIES),
  ambientG: new Float32Array(MAX_ENTITIES),
  ambientB: new Float32Array(MAX_ENTITIES),
  bgmLayer: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Per-player biome blend state. `current`/`target` hold BiomeRegion entity ids
 * or {@link NO_BIOME} (default vale); `blend` is the 0..1 crossfade progress.
 */
export const ActiveBiome = {
  current: new Uint32Array(MAX_ENTITIES),
  target: new Uint32Array(MAX_ENTITIES),
  blend: new Float32Array(MAX_ENTITIES),
} as const;
