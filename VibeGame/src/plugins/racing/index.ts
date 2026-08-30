export { RacingPlugin } from './plugin';
export {
  vehicleRecipe,
  playerVehicleRecipe,
  aiVehicleRecipe,
  trackRecipe,
  chaseCameraRecipe,
  raceTrackItemBoxRecipe,
  raceTrackRampRecipe,
  hazardsLayoutRecipe,
  raceTrackObstacleRecipe,
} from './plugin';
export {
  Vehicle,
  PlayerVehicle,
  AiDriver,
  ChaseCamera,
  Track,
  RaceTracker,
  HeldItem,
  ItemKind,
  ItemBox,
  ObstacleKind,
  ObstacleMoveMode,
  TrackObstacleState,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
export type { ItemKindValue, ObstacleKindValue } from './components';
export { TrackSpline, createFrame, nodesFromFlatList } from './spline';
export type {
  TrackNode,
  TrackFrame,
  TrackProjection,
  TrackSplineOptions,
} from './spline';
export {
  setTrackSpline,
  attachTrackSpline,
  getTrackSpline,
  getAllTrackEntities,
  getPrimaryTrackEntity,
  clearTrackData,
  addTrackObstacle,
  clearTrackObstacles,
  getTrackObstacles,
  removeTrackObstacles,
  forEachNearbyObstacle,
  repositionTrackObstacle,
  setWorldObstacleTrackIdx,
  addItemBox,
  getItemBoxes,
  clearItemBoxes,
  addTrackObstacleByS,
  getTrackSpaceObstacles,
  removeTrackSpaceObstacles,
  clearTrackSpaceObstacles,
  addTrackRamp,
  getTrackRamps,
  rampAt,
  rampHeightAt,
  clearTrackRamps,
  addOilSlick,
  getOilSlicks,
  removeOilSlick,
  clearOilSlicks,
  addFireball,
  getFireballs,
  removeFireball,
  clearFireballs,
} from './data';
export type {
  TrackObstacle,
  ItemBoxDef,
  TrackSpaceObstacle,
  TrackRamp,
  OilSlick,
  Fireball,
} from './data';
export {
  getRaceState,
  setRaceState,
  resetRaceState,
  isRacingActive,
  restartRace,
  holdRaceOnGrid,
  markRaceReady,
  isRaceReady,
  beginRaceFromQualifying,
  captureQualifyingGrid,
  getQualifyingGrid,
  conditionWetness,
  conditionIsNight,
  conditionGripMul,
} from './race-state';
export type {
  RacePhase,
  RaceState,
  RaceResult,
  RaceSession,
  TrackCondition,
} from './race-state';
export { VehicleControlSystem, placeVehicleOnTrack } from './vehicle-control';
export {
  driftTier,
  evaluateLaunch,
  drawLaunchDelay,
  DRIFT_MIN_SPEED,
  DRIFT_TIER1_S,
  DRIFT_TIER2_S,
  MINI_TURBO_T1_S,
  MINI_TURBO_T2_S,
  LAUNCH_REV_RATE,
  LAUNCH_OVERREV_S,
  LAUNCH_WHEELSPIN_S,
} from './vehicle-control';
export type { LaunchQuality } from './vehicle-control';
export { AiDriverSystem, triggerAiMistake, resetAiMistakes } from './ai-driver';
export type { AiMistakeKind } from './ai-driver';
export {
  RaceDirectorSystem,
  getStandings,
  getVehicleName,
  setVehicleName,
  intervalToNeighbour,
  GRID_FIRST_S,
} from './race-director';
export type { RaceInterval } from './race-director';
export {
  ChaseCameraSystem,
  getCameraModeName,
  CAMERA_MODES,
} from './chase-camera';
export type { CameraModeName } from './chase-camera';
export {
  TrackSpawnSystem,
  HoloPulseSystem,
  getTrackMeshes,
  trackStyles,
} from './track-spawn';
export {
  StartLightsSystem,
  startLightPattern,
  START_LIGHT_COUNT,
} from './start-lights';
export { RaceConditionsSystem } from './conditions';
export { VehicleVisualSystem } from './vehicle-visual';
export { VehicleFxSystem } from './vehicle-fx';
export { EngineAudioSystem, vehicleSfxEdges } from './engine-audio';
export { ItemSystem, useHeldItem, rollItem, ITEM_META } from './items';
export { ItemBoxSystem, ItemBoxVisualSystem } from './item-boxes';
export { TrickSystem, startTrick, startSpinOut, TrickKind } from './tricks';
export type { SpinResult } from './tricks';
export {
  RacingFxSystem,
  pushRacingFx,
  pushRacingBanner,
  drainRacingBanners,
  addImpactShake,
  getImpactShake,
  resetRacingFx,
} from './fx-events';
export type {
  RacingFxEvent,
  RacingFxKind,
  RacingBannerEvent,
} from './fx-events';
export { RampVisualSystem } from './ramps';
export {
  HazardsLayoutSystem,
  setHazardsLayout,
  getHazardsLayout,
  clearHazardsLayout,
  mulberry32,
  generateItemBoxRows,
  generateObstacles,
} from './layouts';
export type {
  HazardsLayoutOptions,
  BoxPlacement,
  ObstacleSpec,
} from './layouts';
export {
  MovingObstacleSystem,
  TrackObstacleVisualSystem,
  getObstacleVisuals,
} from './obstacles';
export { CheckpointSystem, resetCheckpoints } from './checkpoints';
export { GhostSystem } from './ghost';
export { GhostVisualSystem } from './ghost-visual';
export {
  getGhostLap,
  setGhostLap,
  clearGhost,
  resetGhostRecording,
  sampleGhostAtTime,
  ghostDeltaAt,
  ghostProgressU,
  serializeGhostLap,
  parseGhostLap,
  ghostWorldPose,
  GHOST_SECTOR_COUNT,
  sectorIndex,
  sectorBoundaryU,
  completedSector,
} from './ghost';
export type { GhostSample, GhostLap, GhostWorldPose } from './ghost';
export { registerRacingHudFactories } from './hud';
export { buildTrackMeshes } from './track-geometry';
export type { TrackMeshes, TrackStyle } from './track-geometry';
