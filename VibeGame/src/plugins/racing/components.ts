import { MAX_ENTITIES } from '../../core/ecs/constants';

/**
 * Arcade vehicle state (SOA, indexed by entity id).
 *
 * The racing plugin owns vehicle motion outright: a car is a `Transform` entity
 * whose pose is written by {@link VehicleControlSystem} each fixed step from a
 * closed-form arcade model, grounded on the {@link TrackSpline} surface. There
 * is deliberately no rigidbody — a kinematic body fighting Rapier over ride
 * height was the source of the old jitter, the vertical teleports and the
 * "car floats off the hill" bugs, and a dynamic body needs a suspension model
 * to feel anything like a kart.
 *
 * Fields split into three groups: tunables (parsed once), driver inputs (set by
 * the player or the AI each step) and simulation state (owned by the
 * controller, read by camera / HUD / FX).
 */
export const Vehicle = {
  // ---- Tunables -----------------------------------------------------------
  /** Top speed on full throttle (m/s). */
  maxSpeed: new Float32Array(MAX_ENTITIES),
  /** Engine acceleration at zero speed (m/s²); tapers toward `maxSpeed`. */
  accel: new Float32Array(MAX_ENTITIES),
  /** Braking deceleration (m/s²). */
  brake: new Float32Array(MAX_ENTITIES),
  /** Coasting deceleration with no throttle (m/s²). */
  engineBrake: new Float32Array(MAX_ENTITIES),
  /** Top speed in reverse (m/s). */
  reverseSpeed: new Float32Array(MAX_ENTITIES),
  /** Peak yaw rate at low speed (rad/s). */
  maxSteer: new Float32Array(MAX_ENTITIES),
  /** How fast the steer input ramps toward the key state (1/s). */
  steerSpeed: new Float32Array(MAX_ENTITIES),
  /** Lateral grip: fraction of side-slip killed per second (higher = stickier). */
  grip: new Float32Array(MAX_ENTITIES),
  /** Grip multiplier while the handbrake is held (lower = slides more). */
  driftGrip: new Float32Array(MAX_ENTITIES),
  /** Extra acceleration while boosting (m/s²). */
  boostAccel: new Float32Array(MAX_ENTITIES),
  /** Top-speed multiplier while boosting. */
  boostSpeed: new Float32Array(MAX_ENTITIES),
  /** Boost tank capacity (seconds of continuous boost). */
  boostCapacity: new Float32Array(MAX_ENTITIES),
  /** Boost refill rate (units/s) while not boosting. */
  boostRecharge: new Float32Array(MAX_ENTITIES),
  /** Chassis half-length (m) — collision + grid spacing. */
  halfLength: new Float32Array(MAX_ENTITIES),
  /** Chassis half-width (m). */
  halfWidth: new Float32Array(MAX_ENTITIES),
  /** Height of the chassis origin above the road surface (m). */
  rideHeight: new Float32Array(MAX_ENTITIES),

  // ---- Driver input (written by player input or the AI) -------------------
  /** Throttle 0..1. */
  throttle: new Float32Array(MAX_ENTITIES),
  /** Brake 0..1 (also reverses once stopped). */
  brakeInput: new Float32Array(MAX_ENTITIES),
  /** Steering -1..1 (positive = right). */
  steerInput: new Float32Array(MAX_ENTITIES),
  /** Handbrake 0/1. */
  handbrake: new Uint8Array(MAX_ENTITIES),
  /** Boost request 0/1. */
  boostInput: new Uint8Array(MAX_ENTITIES),

  // ---- Simulation state ---------------------------------------------------
  /** Forward speed along the chassis heading (m/s, signed). */
  speed: new Float32Array(MAX_ENTITIES),
  /** Sideways speed in chassis space (m/s, positive = sliding right). */
  lateralSpeed: new Float32Array(MAX_ENTITIES),
  /** World heading (yaw, radians; 0 = +Z). */
  heading: new Float32Array(MAX_ENTITIES),
  /** Current yaw rate (rad/s). */
  yawRate: new Float32Array(MAX_ENTITIES),
  /** Smoothed steer state (-1..1) — the ramped version of `steerInput`. */
  steer: new Float32Array(MAX_ENTITIES),
  /** Arc position along the track (m). */
  trackS: new Float32Array(MAX_ENTITIES),
  /** Signed lateral offset from the centerline (m, positive = right). */
  trackLateral: new Float32Array(MAX_ENTITIES),
  /** Height above the road surface (m). */
  airHeight: new Float32Array(MAX_ENTITIES),
  /** Vertical velocity while airborne (m/s). */
  verticalSpeed: new Float32Array(MAX_ENTITIES),
  /** 1 while the wheels are off the ground. */
  airborne: new Uint8Array(MAX_ENTITIES),
  /** Surface grip multiplier under the wheels (1 = road, <1 = kerb/dirt). */
  surfaceGrip: new Float32Array(MAX_ENTITIES),
  /** Normalised slide amount 0..1 — drives smoke, skid marks and screech. */
  slip: new Float32Array(MAX_ENTITIES),
  /** Remaining boost (same unit as `boostCapacity`). */
  boost: new Float32Array(MAX_ENTITIES),
  /** 1 while boost is actually being spent. */
  boosting: new Uint8Array(MAX_ENTITIES),
  /** Seconds since the last wall/car impact (impact FX + AI recovery). */
  impactTimer: new Float32Array(MAX_ENTITIES),
  /** Engine revs 0..1 (audio + HUD). */
  rpm: new Float32Array(MAX_ENTITIES),
  /** Current gear (1-based, 0 = reverse) — audio + HUD only. */
  gear: new Uint8Array(MAX_ENTITIES),

  // ---- Visual juice (read by the chassis visual) --------------------------
  /** Accumulated wheel rotation (rad). */
  wheelSpin: new Float32Array(MAX_ENTITIES),
  /** Steering angle applied to the front wheels (rad). */
  wheelSteer: new Float32Array(MAX_ENTITIES),
  /** Body roll (rad, leans out of the corner). */
  roll: new Float32Array(MAX_ENTITIES),
  /** Body pitch (rad, dives on the brakes). */
  pitch: new Float32Array(MAX_ENTITIES),
  /** Slipstream strength 0..1 — extra accel when drafting a car ahead. */
  draft: new Float32Array(MAX_ENTITIES),
} as const;

/** Tag: the vehicle the local player drives (camera + HUD bind to it). */
export const PlayerVehicle = {
  tag: new Uint8Array(MAX_ENTITIES),
} as const;

/**
 * Tag + tuning for a computer-driven rival. The {@link AiDriverSystem} writes
 * this vehicle's inputs from the racing line.
 */
export const AiDriver = {
  /** 0..1 — how hard this rival drives (corner speed, throttle discipline). */
  skill: new Float32Array(MAX_ENTITIES),
  /** Preferred lateral offset from the racing line (m) — keeps rivals apart. */
  lineOffset: new Float32Array(MAX_ENTITIES),
  /** Rubber-band strength 0..1 (0 = none, 1 = strongly matches the player). */
  rubberBand: new Float32Array(MAX_ENTITIES),
  /** Internal: smoothed steering target, and a per-driver noise phase. */
  steerState: new Float32Array(MAX_ENTITIES),
  noisePhase: new Float32Array(MAX_ENTITIES),
  /** Seconds spent making no progress along the track → recovery nudge. */
  stuckTimer: new Float32Array(MAX_ENTITIES),
  /** Arc position when the stuck check last sampled it (m). */
  progressS: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * A racing circuit. The polyline lives in a sidecar ({@link getTrackSpline})
 * because bitecs stores only numbers; this component holds the scalars other
 * systems query.
 */
export const Track = {
  /** Laps required to finish. */
  totalLaps: new Uint32Array(MAX_ENTITIES),
  /** Total circuit length (m), filled once the spline is built. */
  length: new Float32Array(MAX_ENTITIES),
  /** Default road width (m). */
  width: new Float32Array(MAX_ENTITIES),
  /** Width of the drivable-but-slow shoulder either side of the road (m). */
  shoulder: new Float32Array(MAX_ENTITIES),
  /** 1 when barriers stop the car at the shoulder edge. */
  walls: new Uint8Array(MAX_ENTITIES),
  /** Number of checkpoints split across the lap (Time Trial). 0 = disabled. */
  checkpointCount: new Uint8Array(MAX_ENTITIES),
  /**
   * Deck-above-ground distance (m) that turns a stretch into a viaduct: deck
   * box + pylons are built under it. `0` = the circuit never leaves the ground.
   * Match `<Road flatten-viaduct-clearance>` so the terrain is not graded under
   * a span that also gets columns.
   */
  viaductClearance: new Float32Array(MAX_ENTITIES),
  /** Arc spacing between pylons (m); 0 = engine default. */
  pylonSpacing: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * Per-vehicle race progress. Owned by {@link RaceDirectorSystem}.
 *
 * Progress is continuous: `lap * trackLength + trackS`. Lap counting watches the
 * arc position wrap forward past the start line, which — unlike the old
 * "fraction jumped from 0.9 to 0.1" heuristic — cannot be fooled by a car
 * reversing over the line or by a projection glitch on a crossover.
 */
export const RaceTracker = {
  /** The track entity this vehicle races on. */
  track: new Uint32Array(MAX_ENTITIES),
  /** Completed laps. */
  lap: new Uint32Array(MAX_ENTITIES),
  /** Arc position last frame (m) — wrap detector. */
  lastS: new Float32Array(MAX_ENTITIES),
  /** Total distance covered (m); the ranking key. */
  distance: new Float32Array(MAX_ENTITIES),
  /** Race clock when the current lap started (s). */
  lapStartTime: new Float32Array(MAX_ENTITIES),
  /** Best lap so far (s); -1 = none yet. */
  bestLapTime: new Float32Array(MAX_ENTITIES),
  /** Last completed lap (s); -1 = none yet. */
  lastLapTime: new Float32Array(MAX_ENTITIES),
  /** 1 once the car has taken the chequered flag. */
  finished: new Uint8Array(MAX_ENTITIES),
  /** Race time at the finish (s). */
  finishTime: new Float32Array(MAX_ENTITIES),
  /** Live race position (1 = leading). */
  position: new Uint32Array(MAX_ENTITIES),
  /** 1 while the car is pointing against the racing direction. */
  wrongWay: new Uint8Array(MAX_ENTITIES),
  /** Seconds spent going the wrong way (debounce for the HUD warning). */
  wrongWayTimer: new Float32Array(MAX_ENTITIES),
  /** Grid slot (0 = pole) used when placing cars for the start / a restart. */
  gridSlot: new Uint32Array(MAX_ENTITIES),
  /** Index of the last checkpoint the car has passed (Time Trial). */
  lastCheckpointIndex: new Int16Array(MAX_ENTITIES),
  /** Arc position of the last checkpoint the car has passed. */
  lastCheckpointS: new Float32Array(MAX_ENTITIES),
  /** 1 if the car has been respawned this step (HUD flash). */
  respawnFlash: new Uint8Array(MAX_ENTITIES),
  /** Seconds spent off-track (used by the respawn trigger). */
  offTrackTimer: new Float32Array(MAX_ENTITIES),
  /** Seconds spent making no progress along the track (stuck respawn). */
  stuckTimer: new Float32Array(MAX_ENTITIES),
  /** Arc position when the stuck check last sampled it (m). */
  stuckS: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * Follow camera. Trails the car's heading and rides the track's up vector so it
 * banks with the road instead of staying stubbornly world-up.
 */
export const ChaseCamera = {
  /** Vehicle entity being followed. */
  target: new Uint32Array(MAX_ENTITIES),
  /** Distance behind the car (m). */
  distance: new Float32Array(MAX_ENTITIES),
  /** Height above the car (m). */
  height: new Float32Array(MAX_ENTITIES),
  /** Positional follow time constant (s). */
  followLag: new Float32Array(MAX_ENTITIES),
  /** Heading trail time constant (s). */
  turnLag: new Float32Array(MAX_ENTITIES),
  /** Look-ahead distance in front of the car (m). */
  lookAhead: new Float32Array(MAX_ENTITIES),
  /** Resting field of view (deg). */
  fovBase: new Float32Array(MAX_ENTITIES),
  /** Extra FOV at top speed (deg). */
  fovBoost: new Float32Array(MAX_ENTITIES),
  /** Active view: 0 chase, 1 close chase, 2 hood, 3 orbit (replay/podium). */
  mode: new Uint8Array(MAX_ENTITIES),

  // Smoothed internal state.
  followX: new Float32Array(MAX_ENTITIES),
  followY: new Float32Array(MAX_ENTITIES),
  followZ: new Float32Array(MAX_ENTITIES),
  smoothYaw: new Float32Array(MAX_ENTITIES),
  upX: new Float32Array(MAX_ENTITIES),
  upY: new Float32Array(MAX_ENTITIES),
  upZ: new Float32Array(MAX_ENTITIES),
  fov: new Float32Array(MAX_ENTITIES),
  orbitAngle: new Float32Array(MAX_ENTITIES),
  initialized: new Uint8Array(MAX_ENTITIES),
} as const;

/** Optional GLB chassis per vehicle (`<Vehicle model-url=…>`). */
export const VehicleModelUrls = new Map<number, string>();

/** Chassis yaw correction in degrees per vehicle; 0 = the model already faces +Z. */
export const VehicleModelYaw = new Map<number, number>();

/**
 * Target chassis length in metres per vehicle. Generated GLBs arrive at
 * arbitrary scale, so the visual system fits the model to this length instead
 * of trusting the file (that is why the old example rendered a kart the size of
 * a building).
 */
export const VehicleModelLength = new Map<number, number>();

/** Body tint per vehicle, applied to the procedural chassis. */
export const VehicleColors = new Map<number, number>();

/**
 * Power-up loadout a car brings to the race.
 *
 * Slots are 0 (Pulse), 1 (Sidewinder), 2 (Shield). Ammos decrement on use and
 * recharge over time. Cooldown state is read by the HUD and the wire-up system.
 */
export const PowerUp = {
  /** Active ammo per slot (0..capacity). */
  ammo0: new Float32Array(MAX_ENTITIES),
  ammo1: new Float32Array(MAX_ENTITIES),
  ammo2: new Float32Array(MAX_ENTITIES),
  /** Maximum ammo per slot (HUD cap). */
  cap0: new Float32Array(MAX_ENTITIES),
  cap1: new Float32Array(MAX_ENTITIES),
  cap2: new Float32Array(MAX_ENTITIES),
  /** Seconds since the slot was used (for the cooldown overlay). */
  cd0: new Float32Array(MAX_ENTITIES),
  cd1: new Float32Array(MAX_ENTITIES),
  cd2: new Float32Array(MAX_ENTITIES),
  /** Total cooldown duration per slot. */
  cdTotal0: new Float32Array(MAX_ENTITIES),
  cdTotal1: new Float32Array(MAX_ENTITIES),
  cdTotal2: new Float32Array(MAX_ENTITIES),
  /** 1 if Shield has absorbed a respawn latched for `SHIELD_LATCH_S`. */
  shieldArmed: new Uint8Array(MAX_ENTITIES),
  /** Pulse boost time remaining (s). 0 = idle. */
  pulseBoost: new Float32Array(MAX_ENTITIES),
} as const;

/** Pickup orb placed on the track surface. 0=Pulse, 1=Sidewinder, 2=Shield. */
export const PickupKind = {
  Pulse: 0,
  Sidewinder: 1,
  Shield: 2,
} as const;
export type PickupKindValue = (typeof PickupKind)[keyof typeof PickupKind];

/**
 * Active state for a track-placed pickup orb. Orbs are pooled (ring buffer) so
 * the example can scatter ~20 of them without GC churn.
 */
export const PickupOrb = {
  /** Active lifetime remaining (s). 0 = available in the ring buffer. */
  ttl: new Float32Array(MAX_ENTITIES),
  /** Kind index (0/1/2). */
  kind: new Uint8Array(MAX_ENTITIES),
  /** Arc position along the track (m). */
  s: new Float32Array(MAX_ENTITIES),
  /** Lateral offset from the centerline (m). */
  lateral: new Float32Array(MAX_ENTITIES),
  /** Respawn-after-collect period in seconds (0 = single-use). */
  respawnAfter: new Float32Array(MAX_ENTITIES),
} as const;

/**
 * Track-side obstacle kinds. 0 barrel, 1 drone, 2 gate.
 *
 * Each kind is just a visual hint for the obstacle visual system; physics is
 * identical (a circle obstacle). The Sidewinder probe nudges the one nearest
 * the player forward-and-right.
 */
export const ObstacleKind = {
  Barrel: 0,
  Drone: 1,
  Gate: 2,
} as const;
export type ObstacleKindValue =
  (typeof ObstacleKind)[keyof typeof ObstacleKind];

/**
 * Active state for a track-side obstacle. The position is stored in track
 * space (`s`, `lateral`) so the visual system and the sidewinder test can
 * resolve it to world XYZ without re-querying the spline.
 */
export const TrackObstacleState = {
  /** Arc position along the track (m). */
  s: new Float32Array(MAX_ENTITIES),
  /** Lateral offset from the centerline (m). */
  lateral: new Float32Array(MAX_ENTITIES),
  /** Collision radius (m). */
  radius: new Float32Array(MAX_ENTITIES),
  /** Bounce factor (0..1) — speed retained after a hit. */
  bounce: new Float32Array(MAX_ENTITIES),
  /** Kind index (0/1/2). */
  kind: new Uint8Array(MAX_ENTITIES),
  /** Pitch (rad/s) for spinning decorations (barrel + drone). */
  spin: new Float32Array(MAX_ENTITIES),
  /** Vertical hover offset (drone only). */
  hover: new Float32Array(MAX_ENTITIES),
} as const;
