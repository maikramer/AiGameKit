export {
  Terrain,
  TerrainChunk,
  TerrainDebugInfo,
  TerrainPad,
} from './components';
export { TerrainPlugin } from './plugin';
export { describeTerrainPending, terrainReady } from './ready-gate';
export { terrainRecipe } from './recipes';
export {
  getTerrainContext,
  getTerrainHeightmapUrl,
  registerHeightmapReloadCallback,
  registerGroundMutationCallback,
  fireGroundMutationCallbacks,
  setTerrainHeightmapUrl,
  getTerrainTextureUrl,
  setTerrainTextureUrl,
  swapTerrainTexture,
  setTerrainSplat,
  getTerrainSplat,
} from './utils';
export type { TerrainEntityData, TerrainSplatConfig } from './utils';
export {
  createFlatSampler,
  createHeightmapSampler,
  loadHeightmapFromUrl,
  sampleTerrainHeight,
  getGroundHeight,
} from './height-sampler';
export type { HeightSamplerData } from './height-sampler';
export {
  getTerrainHeightAt,
  isTerrainColliderAt,
  findNearestTerrainEntity,
  setTerrainWireframe,
  reloadTerrainHeightmap,
  getTerrainStats,
} from './terrain-queries';
export { refreshChunkResolutions, TerrainLodSelectSystem } from './systems';
export {
  selectChunks,
  chunkKey,
  resolutionForLevel,
  effectiveResolution,
  deepestLeafAabb,
  meshSurfaceResolutionForPoint,
} from './lod-select';
export type { ChunkDesc } from './lod-select';
export { buildChunkGeometry } from './chunk-geometry';
export {
  buildDensityMap,
  applyOverride,
  boostAt,
  maxBoostOverAabb,
} from './density-map';
export type { DensityMap, WorldAabb, BuildDensityOptions } from './density-map';
export {
  serializeAhgt,
  parseAhgt,
  AHGT_MAGIC,
  AHGT_VERSION,
} from './ahgt-format';
export type { AhgtMeta } from './ahgt-format';
export { loadHeightfield } from './ahgt-loader';
export { flattenRect } from './flatten';
export type { FlattenRectOpts } from './flatten';
export {
  applyHeightBrush,
  forEachTexelInAabb,
  minEffectiveFalloff,
  minEffectiveWidth,
  rebuildTerrainDerivatives,
  samplerTexelStep,
  texelIndexRange,
} from './height-brush';
export type {
  BrushMode,
  BrushSample,
  HeightBrush,
  TexelAabb,
} from './height-brush';
export {
  corridorAabb,
  forEachCorridorSegment,
  nearestOnPolyline,
  segmentAabb,
} from './corridor';
export type { CorridorAabb, NearestOnPolyline } from './corridor';
export {
  applyCorridorDensity,
  applyFeatureDensity,
  densityLeafPad,
} from './ground-mutation';
export { TerrainPadApplySystem, terrainPadParser } from './pad-systems';
export { terrainPadRecipe } from './recipes';
export {
  getGroundBrushes,
  registerGroundBrush,
  unregisterGroundBrush,
  clearGroundBrushes,
  pointInPadCore,
  pointInRoadCorridor,
  isPointOnRoad,
  brushIntersectsBounds,
} from './brush-registry';
export type { GroundBrush, GroundBrushKind } from './brush-registry';
