export { RacingPlugin } from './plugin';
export {
  vehicleRecipe,
  playerVehicleRecipe,
  trackRecipe,
  chaseCameraRecipe,
} from './plugin';
export {
  Vehicle,
  ChaseCamera,
  Track,
  RaceTracker,
  PlayerVehicle,
} from './components';
export {
  setTrackData,
  getTrackData,
  computeTrackMetrics,
  sampleProgress,
  sampleElevation,
  sampleSection,
  clearTrackData,
  getAllTrackEntities,
} from './data';
export type { TrackData, TrackMetrics, ProgressSample } from './data';
export {
  getRaceState,
  setRaceState,
  resetRaceState,
  isRacingActive,
} from './race-state';
export type { RacePhase, RaceState } from './race-state';
export {
  VehicleControlSystem,
} from './vehicle-control';
export { ChaseCameraSystem } from './chase-camera';
export { RaceTrackerSystem } from './race-tracker';
export { registerRacingHudFactories } from './hud';
export { buildTrackMeshes } from './track-geometry';
export type { TrackMeshes } from './track-geometry';
