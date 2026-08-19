import {
  defineComponent,
  F32,
  I16,
  U32,
  U8,
} from '../../core/ecs/component-storage';

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
export const Vehicle = defineComponent({
  // ---- Tunables -----------------------------------------------------------
  /** Top speed on full throttle (m/s). */
  maxSpeed: F32,
  /** Engine acceleration at zero speed (m/s²); tapers toward `maxSpeed`. */
  accel: F32,
  /** Braking deceleration (m/s²). */
  brake: F32,
  /** Coasting deceleration with no throttle (m/s²). */
  engineBrake: F32,
  /** Top speed in reverse (m/s). */
  reverseSpeed: F32,
  /** Peak yaw rate at low speed (rad/s). */
  maxSteer: F32,
  /** How fast the steer input ramps toward the key state (1/s). */
  steerSpeed: F32,
  /** Lateral grip: fraction of side-slip killed per second (higher = stickier). */
  grip: F32,
  /** Grip multiplier while the handbrake is held (lower = slides more). */
  driftGrip: F32,
  /** Extra acceleration while boosting (m/s²). */
  boostAccel: F32,
  /** Top-speed multiplier while boosting. */
  boostSpeed: F32,
  /** Boost tank capacity (seconds of continuous boost). */
  boostCapacity: F32,
  /** Boost refill rate (units/s) while not boosting. */
  boostRecharge: F32,
  /** Chassis half-length (m) — collision + grid spacing. */
  halfLength: F32,
  /** Chassis half-width (m). */
  halfWidth: F32,
  /** Height of the chassis origin above the road surface (m). */
  rideHeight: F32,

  // ---- Driver input (written by player input or the AI) -------------------
  /** Throttle 0..1. */
  throttle: F32,
  /** Brake 0..1 (also reverses once stopped). */
  brakeInput: F32,
  /** Steering -1..1 (positive = right). */
  steerInput: F32,
  /** Handbrake 0/1. */
  handbrake: U8,
  /** Boost request 0/1. */
  boostInput: U8,

  // ---- Simulation state ---------------------------------------------------
  /** Forward speed along the chassis heading (m/s, signed). */
  speed: F32,
  /** Sideways speed in chassis space (m/s, positive = sliding right). */
  lateralSpeed: F32,
  /** World heading (yaw, radians; 0 = +Z). */
  heading: F32,
  /** Current yaw rate (rad/s). */
  yawRate: F32,
  /** Smoothed steer state (-1..1) — the ramped version of `steerInput`. */
  steer: F32,
  /** Arc position along the track (m). */
  trackS: F32,
  /** Signed lateral offset from the centerline (m, positive = right). */
  trackLateral: F32,
  /** Height above the road surface (m). */
  airHeight: F32,
  /** Vertical velocity while airborne (m/s). */
  verticalSpeed: F32,
  /** 1 while the wheels are off the ground. */
  airborne: U8,
  /** Surface grip multiplier under the wheels (1 = road, <1 = kerb/dirt). */
  surfaceGrip: F32,
  /** Normalised slide amount 0..1 — drives smoke, skid marks and screech. */
  slip: F32,
  /** Remaining boost (same unit as `boostCapacity`). */
  boost: F32,
  /** 1 while boost is actually being spent. */
  boosting: U8,
  /** Seconds since the last wall/car impact (impact FX + AI recovery). */
  impactTimer: F32,
  /** Active stunt kind (0 none, 1 roll left, 2 roll right, 3 front flip, 4 360). */
  trickKind: U8,
  /** Stunt rotation accumulated so far (rad) — drives the chassis visual. */
  trickSpin: F32,
  /** 1 while a stunt is in progress mid-air. */
  trickActive: U8,
  /** Seconds of spin-out remaining (>0 = out of control). */
  spinOutTimer: F32,
  /** Spin-out duration when it triggered (visual progress denominator). */
  spinOutTotal: F32,
  /** Engine revs 0..1 (audio + HUD). */
  rpm: F32,
  /** Current gear (1-based, 0 = reverse) — audio + HUD only. */
  gear: U8,

  // ---- Visual juice (read by the chassis visual) --------------------------
  /** Accumulated wheel rotation (rad). */
  wheelSpin: F32,
  /** Steering angle applied to the front wheels (rad). */
  wheelSteer: F32,
  /** Body roll (rad, leans out of the corner). */
  roll: F32,
  /** Body pitch (rad, dives on the brakes). */
  pitch: F32,
  /** Slipstream strength 0..1 — extra accel when drafting a car ahead. */
  draft: F32,
});

/** Tag: the vehicle the local player drives (camera + HUD bind to it). */
export const PlayerVehicle = defineComponent({
  tag: U8,
});

/**
 * Tag + tuning for a computer-driven rival. The {@link AiDriverSystem} writes
 * this vehicle's inputs from the racing line.
 */
export const AiDriver = defineComponent({
  /** 0..1 — how hard this rival drives (corner speed, throttle discipline). */
  skill: F32,
  /** Preferred lateral offset from the racing line (m) — keeps rivals apart. */
  lineOffset: F32,
  /** Rubber-band strength 0..1 (0 = none, 1 = strongly matches the player). */
  rubberBand: F32,
  /** Internal: smoothed steering target, and a per-driver noise phase. */
  steerState: F32,
  noisePhase: F32,
  /** Seconds spent making no progress along the track → recovery nudge. */
  stuckTimer: F32,
  /** Arc position when the stuck check last sampled it (m). */
  progressS: F32,
});

/**
 * A racing circuit. The polyline lives in a sidecar ({@link getTrackSpline})
 * because bitecs stores only numbers; this component holds the scalars other
 * systems query.
 */
export const Track = defineComponent({
  /** Laps required to finish. */
  totalLaps: U32,
  /** Total circuit length (m), filled once the spline is built. */
  length: F32,
  /** Default road width (m). */
  width: F32,
  /** Width of the drivable-but-slow shoulder either side of the road (m). */
  shoulder: F32,
  /** 1 when barriers stop the car at the shoulder edge. */
  walls: U8,
  /** Number of checkpoints split across the lap (Time Trial). 0 = disabled. */
  checkpointCount: U8,
  /**
   * Deck-above-ground distance (m) that turns a stretch into a viaduct: deck
   * box + pylons are built under it. `0` = the circuit never leaves the ground.
   * Match `<Road flatten-viaduct-clearance>` so the terrain is not graded under
   * a span that also gets columns.
   */
  viaductClearance: F32,
  /** Arc spacing between pylons (m); 0 = engine default. */
  pylonSpacing: F32,
});

/**
 * Per-vehicle race progress. Owned by {@link RaceDirectorSystem}.
 *
 * Progress is continuous: `lap * trackLength + trackS`. Lap counting watches the
 * arc position wrap forward past the start line, which — unlike the old
 * "fraction jumped from 0.9 to 0.1" heuristic — cannot be fooled by a car
 * reversing over the line or by a projection glitch on a crossover.
 */
export const RaceTracker = defineComponent({
  /** The track entity this vehicle races on. */
  track: U32,
  /** Completed laps. */
  lap: U32,
  /** Arc position last frame (m) — wrap detector. */
  lastS: F32,
  /** Total distance covered (m); the ranking key. */
  distance: F32,
  /** Race clock when the current lap started (s). */
  lapStartTime: F32,
  /** Best lap so far (s); -1 = none yet. */
  bestLapTime: F32,
  /** Last completed lap (s); -1 = none yet. */
  lastLapTime: F32,
  /** 1 once the car has taken the chequered flag. */
  finished: U8,
  /** Race time at the finish (s). */
  finishTime: F32,
  /** Live race position (1 = leading). */
  position: U32,
  /** 1 while the car is pointing against the racing direction. */
  wrongWay: U8,
  /** Seconds spent going the wrong way (debounce for the HUD warning). */
  wrongWayTimer: F32,
  /** Grid slot (0 = pole) used when placing cars for the start / a restart. */
  gridSlot: U32,
  /** Index of the last checkpoint the car has passed (Time Trial). */
  lastCheckpointIndex: I16,
  /** Arc position of the last checkpoint the car has passed. */
  lastCheckpointS: F32,
  /** 1 if the car has been respawned this step (HUD flash). */
  respawnFlash: U8,
  /** Seconds spent off-track (used by the respawn trigger). */
  offTrackTimer: F32,
  /** Seconds spent making no progress along the track (stuck respawn). */
  stuckTimer: F32,
  /** Arc position when the stuck check last sampled it (m). */
  stuckS: F32,
});

/**
 * Follow camera. Trails the car's heading and rides the track's up vector so it
 * banks with the road instead of staying stubbornly world-up.
 */
export const ChaseCamera = defineComponent({
  /** Vehicle entity being followed. */
  target: U32,
  /** Distance behind the car (m). */
  distance: F32,
  /** Height above the car (m). */
  height: F32,
  /** Positional follow time constant (s). */
  followLag: F32,
  /** Heading trail time constant (s). */
  turnLag: F32,
  /** Look-ahead distance in front of the car (m). */
  lookAhead: F32,
  /** Resting field of view (deg). */
  fovBase: F32,
  /** Extra FOV at top speed (deg). */
  fovBoost: F32,
  /** Active view: 0 chase, 1 close chase, 2 hood, 3 orbit (replay/podium). */
  mode: U8,

  // Smoothed internal state.
  followX: F32,
  followY: F32,
  followZ: F32,
  smoothYaw: F32,
  upX: F32,
  upY: F32,
  upZ: F32,
  fov: F32,
  orbitAngle: F32,
  initialized: U8,
});

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
 * The single item slot every vehicle carries (Mario-Kart style).
 *
 * Collecting an item box spins a roulette (`rouletteTimer`); when it stops the
 * rolled item lands in `item`. One item at a time — collect while holding and
 * the box is wasted, exactly like the genre expects.
 */
export const HeldItem = defineComponent({
  /** Held item (see {@link ItemKind}). 0 = empty hands. */
  item: U8,
  /** >0 while the collected box's roulette is still spinning (s). */
  rouletteTimer: F32,
  /** 1 while Shield is latched (absorbs one hit / respawn). */
  shieldArmed: U8,
  /** Seconds before a latched Shield drops on its own. */
  shieldTime: F32,
  /** Turbo boost time remaining (s). 0 = idle. */
  turboTime: F32,
});

/** Items an item box can hand out. Keep contiguous — arrays index by this. */
export const ItemKind = {
  None: 0,
  Turbo: 1,
  Fireball: 2,
  Oil: 3,
  Shield: 4,
} as const;
export type ItemKindValue = (typeof ItemKind)[keyof typeof ItemKind];

/**
 * A collectible item box on the track surface. What is inside is only decided
 * on collection (position-weighted roll), so the box itself carries no kind.
 */
export const ItemBox = defineComponent({
  /** Active lifetime remaining (s). 0 = available in the ring buffer. */
  ttl: F32,
  /** Arc position along the track (m). */
  s: F32,
  /** Lateral offset from the centerline (m). */
  lateral: F32,
  /** Respawn-after-collect period in seconds (0 = single-use). */
  respawnAfter: F32,
});

/**
 * Track-side obstacle kinds. 0 barrel, 1 drone, 2 gate, 3 crate.
 *
 * Each kind is just a visual hint for the obstacle visual system; physics is
 * identical (a circle obstacle), except crates which shatter on the first hit.
 */
export const ObstacleKind = {
  Barrel: 0,
  Drone: 1,
  Gate: 2,
  Crate: 3,
} as const;
export type ObstacleKindValue =
  (typeof ObstacleKind)[keyof typeof ObstacleKind];

/** How a track obstacle moves. 0 parked, 1 sweeps side to side, 2 travels on. */
export const ObstacleMoveMode = {
  Static: 0,
  Sweep: 1,
  Travel: 2,
} as const;

/**
 * Active state for a track-side obstacle. The position is stored in track
 * space (`s`, `lateral`) so the visual system and the collision test can
 * resolve it to world XYZ without re-querying the spline.
 */
export const TrackObstacleState = defineComponent({
  /** Arc position along the track (m). */
  s: F32,
  /** Lateral offset from the centerline (m). */
  lateral: F32,
  /** Collision radius (m). */
  radius: F32,
  /** Bounce factor (0..1) — speed retained after a hit. */
  bounce: F32,
  /** Kind index (see {@link ObstacleKind}). */
  kind: U8,
  /** Pitch (rad/s) for spinning decorations (barrel + drone). */
  spin: F32,
  /** Vertical hover offset (drone only). */
  hover: F32,
  /** Movement mode (see {@link ObstacleMoveMode}). */
  moveMode: U8,
  /** Sweep/travel speed (m/s or rad/s of the sweep phase). */
  moveSpeed: F32,
  /** Sweep half-amplitude (m). */
  moveRange: F32,
  /** Sweep phase offset (rad). */
  movePhase: F32,
  /** Rest arc position the movement oscillates around (m). */
  baseS: F32,
  /** Rest lateral offset the movement oscillates around (m). */
  baseLateral: F32,
  /** 1 when the obstacle shatters on the first hit (crate). */
  breakable: U8,
  /** >0 while broken and waiting to reform (s). */
  cooldown: F32,
});
