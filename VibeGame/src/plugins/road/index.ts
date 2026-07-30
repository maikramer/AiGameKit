export {
  RoadPlugin,
  roadRecipe,
  roadNetworkRecipe,
  wayRecipe,
  segmentRecipe,
} from './plugin';
export { Road, getRoadData, setRoadData } from './components';
export type { RoadData } from './components';
export {
  RoadApplySystem,
  RoadRetargetSystem,
  ROADBED_OVERHANG,
  buildRoadHeightAt,
  collectRoadJunctionInputs,
  maxNeighborhoodHeight,
  roadJunctionEnds,
} from './systems';
export {
  ROAD_MITER_LIMIT,
  densifyPathByHeight,
  distanceToPolyline,
  extendPathEnds,
  makeRoadGeometry,
  resampleRoadPath,
  smoothPath,
} from './geometry';
export type { RoadGeometryOptions } from './geometry';
export {
  ROAD_JUNCTION_CLUSTER,
  ROAD_JUNCTION_END_SNAP,
  ROAD_JUNCTION_SIDE_SLACK,
  ROAD_JUNCTION_TIP_FLARE,
  chainRoleFor,
  detectRoadJunctions,
  emptyFusionPlan,
  junctionNetworkSignature,
  makeFusionWidthAt,
  makeJunctionGeometry,
  makeWidthAtFromVertexWidths,
  planRoadFusion,
  retractPathEnds,
  reverseRoadPath,
  sampleJunctionPlateY,
  stitchEndToEndChains,
  trimPathEnd,
  trimPathStart,
} from './junctions';
export type {
  RoadFusionPlan,
  RoadJunction,
  RoadJunctionArm,
  RoadJunctionInput,
  StitchedRoadChain,
} from './junctions';
export {
  carveRoadCorridor,
  DEFAULT_ROAD_MAX_GRADE,
  DEFAULT_ROAD_PLATFORM_SINK,
  designRoadProfile,
  limitProfileGrade,
  ROAD_PROFILE_SMOOTH_PASSES,
  smoothProfile,
} from './carve';
export type { RoadCorridorOpts } from './carve';
export {
  buildRoadNetworkGraph,
  buildSegmentPathAndWidths,
  expandRoadNetworkToRoads,
  parseFlatXZList,
  parseRoadNetworkElement,
  parseWayXZ,
  pathBetweenWays,
  wayDegrees,
} from './network';
export type {
  RoadNetworkDef,
  RoadNetworkGraph,
  RoadSegmentDef,
  RoadWay,
} from './network';
export {
  ROAD_CROSSING_WIDTH_FLARE,
  ROAD_PROFILES,
  resolveRoadProfile,
} from './profiles';
export type { RoadProfile, RoadProfileName } from './profiles';
export {
  clearRoadNetworkGraphs,
  getRoadNetworkGraphs,
  nearestRoad,
  onRoad,
  pathToWay,
  setRoadNetworkGraph,
  wayPathPolyline,
} from './queries';
export type { NearestRoadHit } from './queries';
