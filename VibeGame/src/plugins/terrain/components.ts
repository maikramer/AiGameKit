import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';
export const Terrain = defineComponent({
  worldSize: F32,
  maxHeight: F32,
  levels: U8,
  resolution: U8,
  lodDistanceRatio: F32,
  lodHysteresis: F32,
  wireframe: U8,
  roughness: F32,
  metalness: F32,
  normalStrength: F32,
  /** Metros por tile de textura (UV em espaço de mundo). 0 = auto (densidade do menor chunk). */
  textureTileSize: F32,
  skirtDepth: F32,
  skirtWidth: F32,
  baseColor: U32,
  heightSmoothing: F32,
  heightSmoothingSpread: F32,
  collisionResolution: U8,
  showChunkBorders: U8,
  snowHeight: F32,
  colorHigh: U32,
  colorMid: U32,
  colorLow: U32,
  colorRock: U32,
  slopeThreshold: F32,
  slopeSoftness: F32,
  /** 0 = height/slope colour tint disabled, 1 = full tint override. The tint
   *  is mixed onto the texture albedo by world-space height (snow caps →
   *  valleys) and geometric slope (rock on steep faces). */
  heightBlendStrength: F32,
  /** 0 = AO map ignored, 1 = full AO multiply. Gates the NAR blue channel. */
  aoStrength: F32,
  /**
   * Procedural noise overlays (first layer = sand patches). Strength 0 disables
   * the sand overlay; >0 blends the shared sand albedo onto flat mid/low ground
   * via world-XZ fBm. Independent of lake/river shore sand.
   */
  noiseSandStrength: F32,
  /** World-space frequency of the sand fBm (higher = smaller patches). */
  noiseSandScale: F32,
  /** fBm must exceed this (0..1) before sand appears — higher = sparser. */
  noiseSandThreshold: F32,
  /** Normalised height (0..1 of maxHeight) where sand patches may start. */
  noiseSandHeightMin: F32,
  /** Normalised height (0..1 of maxHeight) where sand patches fade out. */
  noiseSandHeightMax: F32,
});

/**
 * `<TerrainPad>` — a levelled rounded-rect settlement pad stamped into the
 * heightmap (see flatten.ts). Centre comes from Transform posX/posZ.
 */
export const TerrainPad = defineComponent({
  /** Half-extent of the flat core along X (m). */
  halfX: F32,
  /** Half-extent of the flat core along Z (m). */
  halfZ: F32,
  /**
   * Target height (m).
   *
   * Read together with {@link TerrainPad.heightMode}: in `auto` mode this is an
   * output (the apply system writes the sampled height back so navmesh and
   * placement can read the resolved pad plane), in `absolute` mode it is the
   * input the caller asked for.
   */
  height: F32,
  /**
   * `0` = auto (sample the pre-flatten terrain at the pad centre), `1` =
   * absolute (use `height` verbatim).
   *
   * Needed because `height` alone cannot express intent: a pad explicitly
   * pinned to sea level (`height="0"`) is indistinguishable from an unset one,
   * and terraces stacked from a zero baseline want exactly that.
   */
  heightMode: U8,
  /** Blend ring width (m) back to the original terrain. */
  falloff: F32,
  /** Corner rounding radius (m). */
  cornerRadius: F32,
  applied: U8,
});

export const TerrainChunk = defineComponent({
  field: U32,
  originX: F32,
  originZ: F32,
  size: F32,
  level: U8,
  resolution: U8,
  meshDirty: U8,
});

export const TerrainDebugInfo = defineComponent({
  activeChunks: U32,
  drawCalls: U32,
  totalInstances: U32,
  geometryCount: U32,
  materialCount: U32,
  failedColliderChunks: U32,
  lastUpdated: F32,
});
