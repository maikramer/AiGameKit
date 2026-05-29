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
