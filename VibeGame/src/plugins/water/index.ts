export { WaterPlugin, lakeRecipe } from './plugin';
export { Lake } from './components';
export {
  getWaterBodies,
  isPointInWater,
  registerWaterBody,
  unregisterWaterBody,
  waterLevelAt,
} from './registry';
export type { WaterBody } from './registry';
export { carveBowl, rimHeight } from './carve';
export { LakeApplySystem, WaterAnimSystem } from './systems';
