import { flattenNumberList } from '../../core';
import type { Parser, Plugin, Recipe } from '../../core';
import { degToRad } from '../../shared';
import {
  ChaseCamera,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
import { setTrackData } from './data';
import { ChaseCameraBindSystem } from './chase-camera-bind';
import { ChaseCameraSystem } from './chase-camera';
import { RaceTrackerSystem } from './race-tracker';
import { TrackSpawnSystem } from './track-spawn';
import { VehicleControlSystem } from './vehicle-control';
import { VehicleVisualSystem } from './vehicle-visual';
import { SpeedEffectsSystem } from './speed-effects';
import { VehicleParticlesSystem } from './vehicle-particles';
import { EngineAudioSystem } from './engine-audio';
import { PostProcessingSystem } from './post-processing';
import { NitroSystem } from './nitro-system';
import { NightSkySystem } from './night-sky';
import { MotionTrailSystem } from './motion-trail';
import { WeatherSystem } from './weather-system';
import { CinematicCameraSystem } from './cinematic-camera';
import { registerRacingHudFactories } from './hud';
import { pendingCameraTargets } from './chase-camera-bind';

// ---- Recipes -------------------------------------------------------------

/**
 * `<Vehicle pos="0 0 0" max-speed="42" accel="26" ...>` — an arcade car.
 * Spawns a kinematic-velocity rigidbody (no gravity) so the chassis stays on
 * the road; the vehicle control system owns its forward speed + yaw rate.
 */
export const vehicleRecipe: Recipe = {
  name: 'Vehicle',
  components: ['transform', 'rigidbody', 'collider', 'vehicle', 'race-tracker'],
  overrides: { 'rigidbody.type': 3 }, // kinematic-velocity-based (no gravity)
  parserAttributes: [
    'max-speed',
    'accel',
    'brake',
    'engine-brake',
    'reverse-speed',
    'max-steer',
    'steer-speed',
    'grip',
    'ride-height',
    'model-url',
    'model-yaw',
  ],
};

/** `<PlayerVehicle>` — marker that flags a Vehicle as player-driven + camera/HUD bound. */
export const playerVehicleRecipe: Recipe = {
  name: 'PlayerVehicle',
  components: [
    'vehicle',
    'player-vehicle',
    'race-tracker',
    'rigidbody',
    'collider',
  ],
  overrides: { 'rigidbody.type': 3 }, // kinematic-velocity-based (no gravity)
  parserAttributes: [
    'max-speed',
    'accel',
    'brake',
    'engine-brake',
    'reverse-speed',
    'max-steer',
    'steer-speed',
    'grip',
    'ride-height',
    'model-url',
    'model-yaw',
  ],
};

/**
 * `<RaceTrack centerline="x0 z0 x1 z1 ..." half-width="6" laps="3" closed="true">` —
 * a racing circuit. `centerline` is a flat XZ polyline in world space.
 * (Tag is `RaceTrack`, not `Track`, because `<track>` is an HTML void element
 * for video captions — injecting it via innerHTML silently drops its close tag.)
 */
export const trackRecipe: Recipe = {
  name: 'RaceTrack',
  components: ['transform', 'track'],
  parserAttributes: ['centerline', 'half-width', 'laps', 'closed', 'elevations', 'sections'],
};

/**
 * `<ChaseCamera target="hero" distance="7" height="2.4">` — a follow-behind
 * racing camera bound to a Vehicle. Carries `MainCamera`. If `target` is omitted,
 * auto-binds to the first `PlayerVehicle`.
 */
export const chaseCameraRecipe: Recipe = {
  name: 'ChaseCamera',
  components: ['transform', 'main-camera', 'chase-camera'],
  parserAttributes: [
    'target',
    'distance',
    'height',
    'follow-lag',
    'turn-lag',
    'look-ahead',
    'fov',
    'fov-boost',
  ],
};

// ---- Parsers -------------------------------------------------------------

const vehicleParser: Parser = ({ entity, element }) => {
  const a = element.attributes;
  if (a['max-speed'] !== undefined) Vehicle.maxSpeed[entity] = num(a['max-speed']);
  if (a.accel !== undefined) Vehicle.accel[entity] = num(a.accel);
  if (a.brake !== undefined) Vehicle.brake[entity] = num(a.brake);
  if (a['engine-brake'] !== undefined) Vehicle.engineBrake[entity] = num(a['engine-brake']);
  if (a['reverse-speed'] !== undefined) Vehicle.reverseSpeed[entity] = num(a['reverse-speed']);
  if (a['max-steer'] !== undefined) Vehicle.maxSteer[entity] = num(a['max-steer']);
  if (a['steer-speed'] !== undefined) Vehicle.steerSpeed[entity] = num(a['steer-speed']);
  if (a.grip !== undefined) Vehicle.grip[entity] = num(a.grip);
  if (a['ride-height'] !== undefined) {
    Vehicle.rideHeight[entity] = num(a['ride-height']);
  } else if (a.pos !== undefined) {
    // Author the ride height from the spawn `pos` Y (chassis sits at the level
    // the designer placed it). `pos` may arrive as string or parsed vector.
    const raw = a.pos;
    const y =
      typeof raw === 'string'
        ? parseFloat(raw.trim().split(/\s+/)[1] ?? '0') || 0
        : typeof raw === 'object' && raw !== null && 'y' in raw
          ? Number((raw as { y: number }).y) || 0
          : 0;
    Vehicle.rideHeight[entity] = y;
  } else {
    Vehicle.rideHeight[entity] = 0.6;
  }
  if (a['model-url'] !== undefined && a['model-url'] !== '') {
    VehicleModelUrls.set(entity, String(a['model-url']));
  }
  if (a['model-yaw'] !== undefined && a['model-yaw'] !== '') {
    VehicleModelYaw.set(entity, num(a['model-yaw']));
  }
};

const trackParser: Parser = ({ state, entity, element }) => {
  const a = element.attributes;
  const centerline = flattenNumberList(a.centerline);
  const halfWidth = a['half-width'] !== undefined ? num(a['half-width']) : 6;
  const closed = a.closed !== false && a.closed !== 'false' && a.closed !== 0;
  const laps = a.laps !== undefined ? Math.max(1, Math.floor(num(a.laps))) : 3;
  Track.totalLaps[entity] = laps;

  // Optional 3D data: per-point elevations (metres) and theme sections.
  const elevations =
    a.elevations !== undefined ? flattenNumberList(a.elevations) : undefined;
  const sections =
    a.sections !== undefined
      ? String(a.sections).split(/[\s,]+/).filter(Boolean)
      : undefined;

  setTrackData(state, entity, { centerline, halfWidth, closed, elevations, sections });
};

const chaseCameraParser: Parser = ({ entity, element }) => {
  const a = element.attributes;
  if (a.target !== undefined && a.target !== '') {
    pendingCameraTargets.set(entity, String(a.target));
  }
  if (a.distance !== undefined) ChaseCamera.distance[entity] = num(a.distance);
  if (a.height !== undefined) ChaseCamera.height[entity] = num(a.height);
  if (a['follow-lag'] !== undefined) ChaseCamera.followLag[entity] = num(a['follow-lag']);
  if (a['turn-lag'] !== undefined) ChaseCamera.turnLag[entity] = num(a['turn-lag']);
  if (a['look-ahead'] !== undefined) ChaseCamera.lookAhead[entity] = num(a['look-ahead']);
  if (a.fov !== undefined) ChaseCamera.fovBase[entity] = num(a.fov);
  if (a['fov-boost'] !== undefined) ChaseCamera.fovBoost[entity] = num(a['fov-boost']);
};

function num(v: unknown): number {
  if (typeof v === 'number') return v;
  const n = parseFloat(String(v));
  return Number.isFinite(n) ? n : 0;
}

// ---- Plugin --------------------------------------------------------------

export const RacingPlugin: Plugin = {
  systems: [
    VehicleControlSystem,
    RaceTrackerSystem,
    ChaseCameraBindSystem,
    TrackSpawnSystem,
    VehicleVisualSystem,
    ChaseCameraSystem,
    SpeedEffectsSystem,
    VehicleParticlesSystem,
    EngineAudioSystem,
    PostProcessingSystem,
    NitroSystem,
    NightSkySystem,
    MotionTrailSystem,
    WeatherSystem,
    CinematicCameraSystem,
  ],
  recipes: [vehicleRecipe, playerVehicleRecipe, trackRecipe, chaseCameraRecipe],
  components: {
    vehicle: Vehicle,
    'player-vehicle': PlayerVehicle,
    track: Track,
    'race-tracker': RaceTracker,
    'chase-camera': ChaseCamera,
  },
  config: {
    defaults: {
      vehicle: {
        speed: 0,
        maxSpeed: 42,
        accel: 26,
        brake: 45,
        engineBrake: 7,
        reverseSpeed: 12,
        maxSteer: 2.4,
        steerSpeed: 9,
        grip: 0.9,
        throttle: 0,
        steer: 0,
        handbrake: 0,
        roll: 0,
        pitch: 0,
        wheelSpin: 0,
        rideHeight: 0.6,
      },
      'race-tracker': {
        track: 0,
        lap: 0,
        nextCheckpoint: 0,
        lastProgress: 0,
        lapStartTime: 0,
        bestLapTime: -1,
        lastLapTime: -1,
        finished: 0,
        finishTime: 0,
        progress: 0,
      },
      track: {
        totalLaps: 3,
        length: 0,
      },
      'chase-camera': {
        target: 0,
        distance: 7,
        height: 2.6,
        followLag: 0.18,
        turnLag: 0.22,
        lookAhead: 3,
        fovBase: 72,
        fovBoost: 12,
        followX: 0,
        followY: 0,
        followZ: 0,
        smoothYaw: 0,
        initialized: 0,
      },
    },
    parsers: {
      Vehicle: vehicleParser,
      PlayerVehicle: vehicleParser,
      RaceTrack: trackParser,
      ChaseCamera: chaseCameraParser,
    },
  },
  initialize(_state) {
    // Register HUD widgets (countdown / lap / speed / timer) once on boot.
    registerRacingHudFactories();
  },
};

export { degToRad };
