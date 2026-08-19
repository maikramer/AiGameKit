export { WaterPlugin, lakeRecipe, riverRecipe } from './plugin';
export { Lake, River, getRiverPath, setRiverPath } from './components';
export {
  getWaterBodies,
  isPointInWater,
  isPointNearWater,
  isPointOnWaterBank,
  distanceToWaterAt,
  registerWaterBody,
  unregisterWaterBody,
  waterLevelAt,
} from './registry';
export type { WaterBody } from './registry';
export { carveBowl, rimHeight } from './carve';
export { LakeApplySystem, RiverApplySystem, WaterAnimSystem } from './systems';
export {
  WaterInteractionSystem,
  WaterRippleFxSystem,
  computeWaterDrag,
  spawnWaterRipple,
} from './effects';
