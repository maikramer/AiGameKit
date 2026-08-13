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
  BRIDGE_NATIVE_SPAN_M,
  buildRoadHeightAt,
  resolveBridgeDeckY,
  applyBridgeDeckHeights,
  collectRoadJunctionInputs,
  maxNeighborhoodHeight,
  roadJunctionEnds,
  bridgeDeckSpawnY,
  bridgeDeckWorldContour,
} from './systems';
export {
  pathArcLength,
  pathArcFraction,
  pathPointAtArc,
  bridgeDeckYAt,
  bridgeSpanScaleX,
  bridgeYawDeg,
  bridgeMidXZ,
  bridgeSpanFitRatio,
  bridgeLipCost,
  chooseBridgeLip,
  deckContourAt,
  deckContourCrown,
  deckContourTipY,
  fillContourGaps,
  pickSolidBankY,
  planDeckOriginY,
  BRIDGE_BANK_ABOVE_CHANNEL,
  BRIDGE_DECK_LOCAL_Y,
  BRIDGE_LIP_LEVEL_EPS,
  BRIDGE_MAX_CROWN_ABOVE_LIP,
  BRIDGE_TIP_EMBED_M,
} from './bridge';
export type {
  BridgeDeckContour,
  BridgeGradingCost,
  BridgeLipPlan,
  BridgeLipStrategy,
} from './bridge';
export {
  BRIDGE_CONTOUR_SAMPLES,
  BRIDGE_CONTOUR_TIP_INSET_M,
  probeDeckLocalContour,
} from './bridge-deck';
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
  corridorOverlapsWater,
  waterNoRaiseFloorLocal,
  waterPreserveZonesLocal,
} from './water-guard';
export type { WaterPreserveZones } from './water-guard';
export {
  bridgeDeckCenterXZ,
  crossingOnRiver,
  riverCrossingAt,
} from './river-crossing';
export type { RiverCrossing } from './river-crossing';
export {
  carveBridgeDeckClearance,
  carveRoadCorridor,
  carveRoadApproaches,
  clipPathApproaches,
  bridgeApproachStubs,
  effectiveBridgeApproachMeters,
  bridgeApproachCorridorOpts,
  BRIDGE_APPROACH_METERS,
  BRIDGE_LANDWARD_METERS,
  BRIDGE_INTO_SPAN_METERS,
  BRIDGE_SKIP_CARVE_TEXEL_M,
  BRIDGE_APPROACH_WIDTH_BONUS,
  BRIDGE_APPROACH_MIN_TEXELS,
  BRIDGE_CLEARANCE_WIDTH_BONUS,
  BRIDGE_DECK_UNDERCUT_M,
  BRIDGE_RIBBON_CLEARANCE,
  shouldCarveBridgeApproaches,
  DEFAULT_BERM_WIDTH,
  DEFAULT_PASS_SEPARATION,
  DEFAULT_VIADUCT_RAMP,
  groundedPathRuns,
  flyingPathRuns,
  viaductMask,
  DEFAULT_ROAD_MAX_GRADE,
  DEFAULT_ROAD_PLATFORM_SINK,
  MAX_CORRIDOR_BANK,
  designRoadProfile,
  limitProfileGrade,
  ROAD_PROFILE_SMOOTH_PASSES,
  smoothProfile,
} from './carve';
export type {
  BridgeClearanceOpts,
  FlyingPathRun,
  RoadCorridorOpts,
} from './carve';
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
