export { Terrain, TerrainDebugInfo } from './components';
export { TerrainPlugin } from './plugin';
export { terrainRecipe } from './recipes';
export {
  getTerrainContext,
  getTerrainHeightmapUrl,
  setTerrainHeightmapUrl,
  getTerrainTextureUrl,
  setTerrainTextureUrl,
} from './utils';
export type { TerrainEntityData } from './utils';
export { WebGLTerrainMaterialProvider } from './webgl-material';
export {
  getTerrainHeightAt,
  findNearestTerrainEntity,
  setTerrainWireframe,
  reloadTerrainHeightmap,
  getTerrainStats,
} from './systems';
