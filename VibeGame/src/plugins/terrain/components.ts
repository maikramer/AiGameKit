import { MAX_ENTITIES } from '../../core/ecs/constants';
export const Terrain = {
  worldSize: new Float32Array(MAX_ENTITIES),
  maxHeight: new Float32Array(MAX_ENTITIES),
  levels: new Uint8Array(MAX_ENTITIES),
  resolution: new Uint8Array(MAX_ENTITIES),
  lodDistanceRatio: new Float32Array(MAX_ENTITIES),
  lodHysteresis: new Float32Array(MAX_ENTITIES),
  wireframe: new Uint8Array(MAX_ENTITIES),
  roughness: new Float32Array(MAX_ENTITIES),
  metalness: new Float32Array(MAX_ENTITIES),
  normalStrength: new Float32Array(MAX_ENTITIES),
  /** Metros por tile de textura (UV em espaço de mundo). 0 = auto (densidade do menor chunk). */
  textureTileSize: new Float32Array(MAX_ENTITIES),
  skirtDepth: new Float32Array(MAX_ENTITIES),
  skirtWidth: new Float32Array(MAX_ENTITIES),
  baseColor: new Uint32Array(MAX_ENTITIES),
  heightSmoothing: new Float32Array(MAX_ENTITIES),
  heightSmoothingSpread: new Float32Array(MAX_ENTITIES),
  collisionResolution: new Uint8Array(MAX_ENTITIES),
  showChunkBorders: new Uint8Array(MAX_ENTITIES),
  snowHeight: new Float32Array(MAX_ENTITIES),
  colorHigh: new Uint32Array(MAX_ENTITIES),
  colorMid: new Uint32Array(MAX_ENTITIES),
  colorLow: new Uint32Array(MAX_ENTITIES),
  colorRock: new Uint32Array(MAX_ENTITIES),
  slopeThreshold: new Float32Array(MAX_ENTITIES),
  slopeSoftness: new Float32Array(MAX_ENTITIES),
  /** 0 = height/slope colour tint disabled, 1 = full tint override. The tint
   *  is mixed onto the texture albedo by world-space height (snow caps →
   *  valleys) and geometric slope (rock on steep faces). */
  heightBlendStrength: new Float32Array(MAX_ENTITIES),
  /** 0 = AO map ignored, 1 = full AO multiply. Gates the NAR blue channel. */
  aoStrength: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * `<TerrainPad>` — a levelled rounded-rect settlement pad stamped into the
 * heightmap (see flatten.ts). Centre comes from Transform posX/posZ.
 */
export const TerrainPad = {
  /** Half-extent of the flat core along X (m). */
  halfX: new Float32Array(MAX_ENTITIES),
  /** Half-extent of the flat core along Z (m). */
  halfZ: new Float32Array(MAX_ENTITIES),
  /** Target height (m). 0 = auto: sample the pre-flatten terrain at centre. */
  height: new Float32Array(MAX_ENTITIES),
  /** Blend ring width (m) back to the original terrain. */
  falloff: new Float32Array(MAX_ENTITIES),
  /** Corner rounding radius (m). */
  cornerRadius: new Float32Array(MAX_ENTITIES),
  applied: new Uint8Array(MAX_ENTITIES),
} as const;

export const TerrainChunk = {
  field: new Uint32Array(MAX_ENTITIES),
  originX: new Float32Array(MAX_ENTITIES),
  originZ: new Float32Array(MAX_ENTITIES),
  size: new Float32Array(MAX_ENTITIES),
  level: new Uint8Array(MAX_ENTITIES),
  resolution: new Uint8Array(MAX_ENTITIES),
  meshDirty: new Uint8Array(MAX_ENTITIES),
} as const;

export const TerrainDebugInfo = {
  activeChunks: new Uint32Array(MAX_ENTITIES),
  drawCalls: new Uint32Array(MAX_ENTITIES),
  totalInstances: new Uint32Array(MAX_ENTITIES),
  geometryCount: new Uint32Array(MAX_ENTITIES),
  materialCount: new Uint32Array(MAX_ENTITIES),
  failedColliderChunks: new Uint32Array(MAX_ENTITIES),
  lastUpdated: new Float32Array(MAX_ENTITIES),
} as const;
