import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Arcade vehicle state. A `Vehicle` entity drives itself with a kinematic-velocity
 * rigidbody: the {@link VehicleControlSystem} writes forward velocity from the
 * throttle and a yaw rate from the steer, and Rapier integrates it. This keeps
 * the car stable and snappy (no tipping, no inertia tensor flips) while still
 * colliding with track walls and props. All fields are SOA arrays indexed by eid.
 */
export const Vehicle = {
  // Kinematic target the chassis chases (for visual smoothing, not physics).
  speed: new Float32Array(MAX_ENTITIES), // current forward speed (m/s, signed)
  // Tunables (set once at parse time, then read every fixed step).
  maxSpeed: new Float32Array(MAX_ENTITIES), // top speed (m/s)
  accel: new Float32Array(MAX_ENTITIES), // throttle accel (m/s^2)
  brake: new Float32Array(MAX_ENTITIES), // brake decel (m/s^2)
  engineBrake: new Float32Array(MAX_ENTITIES), // coast decel (m/s^2)
  reverseSpeed: new Float32Array(MAX_ENTITIES), // reverse top speed (m/s)
  maxSteer: new Float32Array(MAX_ENTITIES), // max yaw rate (rad/s) at low speed
  steerSpeed: new Float32Array(MAX_ENTITIES), // how fast steer input ramps (1/s)
  grip: new Float32Array(MAX_ENTITIES), // lateral velocity kill (0..1 per step)
  // Live control state (written by input, read by the controller).
  throttle: new Float32Array(MAX_ENTITIES), // -1..1 (brake/reverse when negative)
  steer: new Float32Array(MAX_ENTITIES), // -1..1 (left negative)
  handbrake: new Uint8Array(MAX_ENTITIES),
  // Visual chassis roll/pitch for juice (written by controller, read by wheel/visual sync).
  roll: new Float32Array(MAX_ENTITIES),
  pitch: new Float32Array(MAX_ENTITIES),
  wheelSpin: new Float32Array(MAX_ENTITIES),
  // Ride height the chassis is clamped to each fixed step (anti-fall). Arcade
  // racers are ground-locked — this keeps the car on the road without a full
  // suspension/raycast-vehicle simulation.
  rideHeight: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * A follow-behind racing camera. Trails the vehicle's heading (not a free yaw),
 * decoupled low-pass follow point, terrain-agnostic (races are on a flat ribbon).
 * Owned by {@link ChaseCameraSystem} (group `draw`, after the generic camera sync
 * so it is the sole authority over the THREE camera transform).
 */
export const ChaseCamera = {
  target: new Uint32Array(MAX_ENTITIES), // target entity id (the vehicle)
  distance: new Float32Array(MAX_ENTITIES), // how far behind (m)
  height: new Float32Array(MAX_ENTITIES), // how high above the target (m)
  followLag: new Float32Array(MAX_ENTITIES), // positional follow time constant (s)
  turnLag: new Float32Array(MAX_ENTITIES), // heading trail time constant (s)
  lookAhead: new Float32Array(MAX_ENTITIES), // look-ahead along heading (m)
  fovBase: new Float32Array(MAX_ENTITIES), // resting FOV (deg)
  fovBoost: new Float32Array(MAX_ENTITIES), // FOV added at top speed (deg)
  // Internal smoothed state (persisted across frames).
  followX: new Float32Array(MAX_ENTITIES),
  followY: new Float32Array(MAX_ENTITIES),
  followZ: new Float32Array(MAX_ENTITIES),
  smoothYaw: new Float32Array(MAX_ENTITIES),
  initialized: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * A racing circuit. `Track` is a sidecar-holding entity: the parser drops the
 * centerline polyline into {@link setTrackData}, and the {@link RaceTrackerSystem}
 * samples it to score vehicle progress and detect laps. One per circuit.
 */
export const Track = {
  totalLaps: new Uint32Array(MAX_ENTITIES),
  // Filled at runtime by resolveTrackMetrics.
  length: new Float32Array(MAX_ENTITIES), // total arc length (m)
} as const;

/**
 * Per-vehicle race progress: lap count, next checkpoint, lap start time, finish
 * time. The {@link RaceTrackerSystem} updates it each frame from the vehicle's
 * projection on the track centerline.
 */
export const RaceTracker = {
  track: new Uint32Array(MAX_ENTITIES), // track entity id
  lap: new Uint32Array(MAX_ENTITIES), // current lap (0-indexed; incremented on cross)
  nextCheckpoint: new Uint32Array(MAX_ENTITIES), // next centerline index to reach
  lastProgress: new Float32Array(MAX_ENTITIES), // last arc-fraction (0..1) seen
  lapStartTime: new Float32Array(MAX_ENTITIES), // realtime at current lap start
  bestLapTime: new Float32Array(MAX_ENTITIES), // best lap duration (s), -1 = none
  lastLapTime: new Float32Array(MAX_ENTITIES), // last lap duration (s), -1 = none
  finished: new Uint8Array(MAX_ENTITIES), // 1 once totalLaps reached
  finishTime: new Float32Array(MAX_ENTITIES), // total race time at finish (s)
  progress: new Float32Array(MAX_ENTITIES), // monotonic progress (laps + frac)
} as const;

/** Tag a vehicle as player-controlled (the one the camera + HUD follow). */
export const PlayerVehicle = {
  tag: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Optional GLB chassis per vehicle: eid → model URL (from `<Vehicle model-url=…>`
 * / `<PlayerVehicle model-url=…>`). When set, {@link VehicleVisualSystem} swaps
 * the procedural body for the loaded GLB (model-yaw rotates it to face +Z).
 */
export const VehicleModelUrls = new Map<number, string>();

/** Optional chassis yaw (degrees, applied around Y) per vehicle; 0 = +Z forward. */
export const VehicleModelYaw = new Map<number, number>();
