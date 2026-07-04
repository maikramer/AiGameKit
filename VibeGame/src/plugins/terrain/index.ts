export {
  Terrain,
  TerrainChunk,
  TerrainDebugInfo,
  TerrainPad,
} from './components';
export { TerrainPlugin } from './plugin';
export { terrainReady } from './ready-gate';
export { terrainRecipe } from './recipes';
export {
  getTerrainContext,
  getTerrainHeightmapUrl,
  registerHeightmapReloadCallback,
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
} from './height-sampler';
export type { HeightSamplerData } from './height-sampler';
export {
  getTerrainHeightAt,
  isTerrainColliderAt,
  findNearestTerrainEntity,
  setTerrainWireframe,
  reloadTerrainHeightmap,
  refreshChunkResolutions,
  getTerrainStats,
  TerrainLodSelectSystem,
} from './systems';
export {
  selectChunks,
  chunkKey,
  resolutionForLevel,
  effectiveResolution,
} from './lod-select';
export type { ChunkDesc } from './lod-select';
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
export { TerrainPadApplySystem, terrainPadParser } from './pad-systems';
export { terrainPadRecipe } from './recipes';
