import { defineComponent, Types } from 'bitecs';

export const Terrain = defineComponent({
  worldSize: Types.f32,
  maxHeight: Types.f32,
  levels: Types.ui8,
  resolution: Types.ui8,
  lodDistanceRatio: Types.f32,
  lodHysteresis: Types.f32,
  wireframe: Types.ui8,
  // Material
  roughness: Types.f32,
  metalness: Types.f32,
  normalStrength: Types.f32,
  skirtDepth: Types.f32,
  // Physics
  collisionResolution: Types.ui8,
  // Debug
  showChunkBorders: Types.ui8,
});

export const TerrainDebugInfo = defineComponent({
  activeChunks: Types.ui32,
  drawCalls: Types.ui32,
  totalInstances: Types.ui32,
  geometryCount: Types.ui32,
  materialCount: Types.ui32,
  lastUpdated: Types.f32,
});
