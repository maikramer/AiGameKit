export { RacingPlugin } from './plugin';
export {
  vehicleRecipe,
  playerVehicleRecipe,
  aiVehicleRecipe,
  trackRecipe,
  chaseCameraRecipe,
  raceTrackPickupRecipe,
  raceTrackObstacleRecipe,
} from './plugin';
export {
  Vehicle,
  PlayerVehicle,
  AiDriver,
  ChaseCamera,
  Track,
  RaceTracker,
  PowerUp,
  PickupKind,
  PickupOrb,
  ObstacleKind,
  TrackObstacleState,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
export type { PickupKindValue, ObstacleKindValue } from './components';
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
  forEachNearbyObstacle,
  repositionTrackObstacle,
  addTrackPickup,
  getTrackPickups,
  clearTrackPickups,
  addTrackObstacleByS,
  getTrackSpaceObstacles,
  clearTrackSpaceObstacles,
} from './data';
export type { TrackObstacle, TrackPickup, TrackSpaceObstacle } from './data';
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
export {
  PowerUpSystem,
  grantPowerUpAmmo,
  usePowerUpSlot,
  getSidewinderBolts,
  resetSidewinderBolts,
} from './powerups';
export type { SidewinderBolt } from './powerups';
export { PickupSystem, PickupVisualSystem } from './pickups';
export { CheckpointSystem, resetCheckpoints } from './checkpoints';
export { TrackObstacleVisualSystem } from './obstacles';
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
