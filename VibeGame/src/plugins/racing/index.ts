export { RacingPlugin } from './plugin';
export {
  vehicleRecipe,
  playerVehicleRecipe,
  aiVehicleRecipe,
  trackRecipe,
  chaseCameraRecipe,
} from './plugin';
export {
  Vehicle,
  PlayerVehicle,
  AiDriver,
  ChaseCamera,
  Track,
  RaceTracker,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
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
} from './data';
export type { TrackObstacle } from './data';
export {
  getRaceState,
  setRaceState,
  resetRaceState,
  isRacingActive,
  restartRace,
  holdRaceOnGrid,
  markRaceReady,
  isRaceReady,
} from './race-state';
export type { RacePhase, RaceState, RaceResult } from './race-state';
export { VehicleControlSystem, placeVehicleOnTrack } from './vehicle-control';
export { AiDriverSystem } from './ai-driver';
export {
  RaceDirectorSystem,
  getStandings,
  getVehicleName,
  setVehicleName,
} from './race-director';
export {
  ChaseCameraSystem,
  getCameraModeName,
  CAMERA_MODES,
} from './chase-camera';
export type { CameraModeName } from './chase-camera';
export { TrackSpawnSystem, getTrackMeshes, trackStyles } from './track-spawn';
export { VehicleVisualSystem } from './vehicle-visual';
export { VehicleFxSystem } from './vehicle-fx';
export { EngineAudioSystem } from './engine-audio';
export { registerRacingHudFactories } from './hud';
export { buildTrackMeshes } from './track-geometry';
export type { TrackMeshes, TrackStyle } from './track-geometry';
