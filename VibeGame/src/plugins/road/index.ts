export { RoadPlugin, roadRecipe } from './plugin';
export { Road, getRoadData, setRoadData } from './components';
export type { RoadData } from './components';
export {
  RoadApplySystem,
  RoadRetargetSystem,
  buildRoadHeightAt,
  maxNeighborhoodHeight,
} from './systems';
export {
  densifyPathByHeight,
  makeRoadGeometry,
  resampleRoadPath,
  smoothPath,
} from './geometry';
export type { RoadGeometryOptions } from './geometry';
export {
  carveRoadCorridor,
  DEFAULT_ROAD_MAX_GRADE,
  designRoadProfile,
  limitProfileGrade,
  smoothProfile,
} from './carve';
export type { RoadCorridorOpts } from './carve';
