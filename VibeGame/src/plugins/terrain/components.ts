import { defineComponent, Types } from 'bitecs';

export const Terrain = defineComponent({
  worldSize: Types.f32,
  maxHeight: Types.f32,
  levels: Types.ui8,
  resolution: Types.ui8,
  lodDistanceRatio: Types.f32,
  lodHysteresis: Types.f32,
  wireframe: Types.ui8,
  roughness: Types.f32,
  metalness: Types.f32,
  normalStrength: Types.f32,
  skirtDepth: Types.f32,
  skirtWidth: Types.f32,
  baseColor: Types.ui32,
  heightSmoothing: Types.f32,
  heightSmoothingSpread: Types.f32,
  // Physics
  collisionResolution: Types.ui8,
  // Debug
  showChunkBorders: Types.ui8,
  // DefaultTerrainMaterial layering
  snowHeight: Types.f32,
  colorHigh: Types.ui32,
  colorMid: Types.ui32,
  colorLow: Types.ui32,
  colorRock: Types.ui32,
  slopeThreshold: Types.f32,
  slopeSoftness: Types.f32,
});

export const TerrainDebugInfo = defineComponent({
  activeChunks: Types.ui32,
  drawCalls: Types.ui32,
  totalInstances: Types.ui32,
  geometryCount: Types.ui32,
  materialCount: Types.ui32,
  failedColliderChunks: Types.ui32,
  lastUpdated: Types.f32,
});
