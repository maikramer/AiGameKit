export { Terrain, TerrainChunk, TerrainDebugInfo } from './components';
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
  findNearestTerrainEntity,
  setTerrainWireframe,
  reloadTerrainHeightmap,
  getTerrainStats,
  TerrainLodSelectSystem,
} from './systems';
export { selectChunks, chunkKey, resolutionForLevel } from './lod-select';
export type { ChunkDesc } from './lod-select';
