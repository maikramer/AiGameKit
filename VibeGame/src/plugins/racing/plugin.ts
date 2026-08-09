import { flattenNumberList } from '../../core';
import type { Parser, Plugin, Recipe, XMLValue } from '../../core';
import {
  AiDriver,
  ChaseCamera,
  PlayerVehicle,
  PowerUp,
  PickupOrb,
  RaceTracker,
  Track,
  TrackObstacleState,
  Vehicle,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
import { setTrackSpline } from './data';
import type { TrackNode } from './spline';
import { AiDriverSystem } from './ai-driver';
import {
  ChaseCameraBindSystem,
  pendingCameraTargets,
} from './chase-camera-bind';
import { ChaseCameraSystem } from './chase-camera';
import { EngineAudioSystem } from './engine-audio';
import { RaceDirectorSystem, setVehicleName } from './race-director';
import { TrackSpawnSystem, HoloPulseSystem, trackStyles } from './track-spawn';
import { VehicleControlSystem } from './vehicle-control';
import { VehicleFxSystem } from './vehicle-fx';
import { VehicleVisualSystem } from './vehicle-visual';
import { registerRacingHudFactories } from './hud';
import { PowerUpSystem } from './powerups';
import { PickupSystem, PickupVisualSystem } from './pickups';
import { CheckpointSystem } from './checkpoints';
import { TrackObstacleVisualSystem } from './obstacles';
import {
  addTrackPickup,
  addTrackObstacleByS,
  addTrackObstacle,
  getTrackSpline,
  getPrimaryTrackEntity,
} from './data';
import { disableDefaultPlayer } from '../startup';
import { logger } from '../../core/utils/logger';

// ---- Recipes ---------------------------------------------------------------

const VEHICLE_ATTRS = [
  'max-speed',
  'accel',
  'brake',
  'engine-brake',
  'reverse-speed',
  'max-steer',
  'steer-speed',
  'grip',
  'drift-grip',
  'boost',
  'boost-accel',
  'boost-speed',
  'boost-recharge',
  'ride-height',
  'half-length',
  'half-width',
  'model-url',
  'model-yaw',
  'model-length',
  'color',
  'driver',
  'loadout',
] as const;

/**
 * `<Vehicle>` — an arcade car. Deliberately has **no rigidbody**: the racing
 * plugin owns vehicle motion (see {@link VehicleControlSystem}), which is what
 * makes grounding, banking and barriers behave.
 */
export const vehicleRecipe: Recipe = {
  name: 'Vehicle',
  components: ['transform', 'vehicle', 'race-tracker', 'power-up'],
  parserAttributes: [...VEHICLE_ATTRS],
};

/** `<PlayerVehicle>` — the car the local player drives. */
export const playerVehicleRecipe: Recipe = {
  name: 'PlayerVehicle',
  components: ['transform', 'vehicle', 'player-vehicle', 'race-tracker', 'power-up'],
  parserAttributes: [...VEHICLE_ATTRS],
};

/** `<AiVehicle skill="0.9" rubber-band="0.6">` — a computer-driven rival. */
export const aiVehicleRecipe: Recipe = {
  name: 'AiVehicle',
  components: ['transform', 'vehicle', 'ai-driver', 'race-tracker', 'power-up'],
  parserAttributes: [...VEHICLE_ATTRS, 'skill', 'rubber-band', 'line-offset'],
};

/**
 * `<RaceTrack centerline="x y z  x y z …" width="14" laps="3">` — the circuit.
 *
 * The centerline is a 3D control polyline (stride 3). Optional parallel lists
 * give per-node road width, banking and theme section. The tag is `RaceTrack`
 * because `<track>` is an HTML void element and would lose its closing tag when
 * the scene is injected as innerHTML.
 */
export const trackRecipe: Recipe = {
  name: 'RaceTrack',
  components: ['transform', 'track'],
  parserAttributes: [
    'centerline',
    'widths',
    'banks',
    'sections',
    'width',
    'laps',
    'closed',
    'shoulder',
    'walls',
    'step',
    'max-bank',
    'checkpoint-count',
    'theme',
    'road-color',
    'apron-color',
    'shoulder-color',
    'wall-color',
  ],
};

/** `<ChaseCamera target="hero" distance="7.5" height="3">` — the race camera. */
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
    'mode',
  ],
};

/** `<RaceTrackPickup s="80" lateral="0" kind="pulse" />` — a power-up orb. */
export const raceTrackPickupRecipe: Recipe = {
  name: 'RaceTrackPickup',
  components: [],
  parserAttributes: ['s', 'lateral', 'kind', 'respawn'],
};

/** `<RaceTrackObstacle s="120" lateral="-2" kind="barrel" />` — a hazard. */
export const raceTrackObstacleRecipe: Recipe = {
  name: 'RaceTrackObstacle',
  components: [],
  parserAttributes: ['s', 'lateral', 'radius', 'bounce', 'kind'],
};

// ---- Parsers ---------------------------------------------------------------

function num(v: unknown, fallback = 0): number {
  if (typeof v === 'number') return v;
  const n = parseFloat(String(v));
  return Number.isFinite(n) ? n : fallback;
}

function color(v: unknown, fallback: number): number {
  if (typeof v === 'number') return v;
  const s = String(v).trim();
  if (s.startsWith('#')) return parseInt(s.slice(1), 16);
  if (s.startsWith('0x')) return parseInt(s.slice(2), 16);
  const n = parseInt(s, 16);
  return Number.isFinite(n) ? n : fallback;
}

function bool(v: XMLValue | undefined, fallback: boolean): boolean {
  if (v === undefined) return fallback;
  return v !== false && v !== 'false' && v !== 0 && v !== '0';
}

const vehicleParser: Parser = ({ entity, element }) => {
  const a = element.attributes;
  // A scene that declares a car is a racing scene: stop the startup plugin from
  // spawning its default walking character at the world origin — which on a
  // circuit is exactly where the start line is. Parsers run before the first
  // `setup` pass, so this lands in time.
  disableDefaultPlayer();
  const setIf = (key: string, arr: Float32Array): void => {
    if (a[key] !== undefined) arr[entity] = num(a[key]);
  };
  setIf('max-speed', Vehicle.maxSpeed);
  setIf('accel', Vehicle.accel);
  setIf('brake', Vehicle.brake);
  setIf('engine-brake', Vehicle.engineBrake);
  setIf('reverse-speed', Vehicle.reverseSpeed);
  setIf('max-steer', Vehicle.maxSteer);
  setIf('steer-speed', Vehicle.steerSpeed);
  setIf('grip', Vehicle.grip);
  setIf('drift-grip', Vehicle.driftGrip);
  setIf('boost-accel', Vehicle.boostAccel);
  setIf('boost-speed', Vehicle.boostSpeed);
  setIf('boost-recharge', Vehicle.boostRecharge);
  setIf('ride-height', Vehicle.rideHeight);
  setIf('half-length', Vehicle.halfLength);
  setIf('half-width', Vehicle.halfWidth);
  if (a.boost !== undefined) {
    Vehicle.boostCapacity[entity] = num(a.boost);
    Vehicle.boost[entity] = num(a.boost);
  }
  if (a['model-url'] !== undefined && a['model-url'] !== '') {
    VehicleModelUrls.set(entity, String(a['model-url']));
  }
  if (a['model-yaw'] !== undefined)
    VehicleModelYaw.set(entity, num(a['model-yaw']));
  if (a['model-length'] !== undefined) {
    VehicleModelLength.set(entity, num(a['model-length'], 2.6));
  }
  if (a.color !== undefined)
    VehicleColors.set(entity, color(a.color, 0xcc2233));
  if (a.driver !== undefined) setVehicleName(entity, String(a.driver));
  else if (a.name !== undefined) setVehicleName(entity, String(a.name));

  // Power-up loadout: `loadout="pulse:2,sidewinder:2,shield:1"`.
  if (a.loadout !== undefined) {
    const slots: [string, number][] = [
      ['pulse', 0],
      ['sidewinder', 1],
      ['shield', 2],
    ];
    const text = String(a.loadout).toLowerCase();
    for (const [name, idx] of slots) {
      const m = new RegExp(`${name}\\s*:\\s*(\\d+)`).exec(text);
      const ammo = m ? Math.max(0, Math.floor(num(m[1]!, 0))) : 0;
      if (ammo > 0) {
        if (idx === 0) {
          PowerUp.cap0[entity] = ammo;
          PowerUp.ammo0[entity] = ammo;
          PowerUp.cdTotal0[entity] = 0.6;
        } else if (idx === 1) {
          PowerUp.cap1[entity] = ammo;
          PowerUp.ammo1[entity] = ammo;
          PowerUp.cdTotal1[entity] = 2.2;
        } else {
          PowerUp.cap2[entity] = ammo;
          PowerUp.ammo2[entity] = ammo;
          PowerUp.cdTotal2[entity] = 1.4;
        }
      }
    }
  }

  // AI tuning (ignored on player/plain vehicles that lack the component).
  if (a.skill !== undefined) AiDriver.skill[entity] = num(a.skill, 0.8);
  if (a['rubber-band'] !== undefined) {
    AiDriver.rubberBand[entity] = num(a['rubber-band'], 0.5);
  }
  if (a['line-offset'] !== undefined) {
    AiDriver.lineOffset[entity] = num(a['line-offset']);
  }
};

const trackParser: Parser = ({ state, entity, element }) => {
  const a = element.attributes;
  const flat = flattenNumberList(a.centerline);
  const widths = a.widths !== undefined ? flattenNumberList(a.widths) : [];
  const banks = a.banks !== undefined ? flattenNumberList(a.banks) : [];
  const sections =
    a.sections !== undefined
      ? String(a.sections)
          .split(/[\s,]+/)
          .filter(Boolean)
      : [];

  const width = a.width !== undefined ? num(a.width, 12) : 12;
  const nodes: TrackNode[] = [];
  const count = Math.floor(flat.length / 3);
  for (let i = 0; i < count; i++) {
    const node: TrackNode = {
      x: flat[i * 3] ?? 0,
      y: flat[i * 3 + 1] ?? 0,
      z: flat[i * 3 + 2] ?? 0,
    };
    if (widths[i] !== undefined) node.width = widths[i];
    if (banks[i] !== undefined) node.bank = banks[i];
    if (sections[i] !== undefined) node.section = sections[i];
    nodes.push(node);
  }

  Track.totalLaps[entity] =
    a.laps !== undefined ? Math.max(1, Math.floor(num(a.laps, 3))) : 3;
  Track.width[entity] = width;
  Track.shoulder[entity] = a.shoulder !== undefined ? num(a.shoulder, 3) : 3;
  Track.walls[entity] = bool(a.walls, true) ? 1 : 0;
  Track.checkpointCount[entity] =
    a['checkpoint-count'] !== undefined
      ? Math.max(0, Math.floor(num(a['checkpoint-count'], 0)))
      : 0;

  trackStyles.set(entity, {
    road:
      a['road-color'] !== undefined
        ? color(a['road-color'], 0x3a3d45)
        : undefined,
    apron:
      a['apron-color'] !== undefined
        ? color(a['apron-color'], 0x4a6b33)
        : undefined,
    shoulderColor:
      a['shoulder-color'] !== undefined
        ? color(a['shoulder-color'], 0x8a7a5c)
        : undefined,
    wall:
      a['wall-color'] !== undefined
        ? color(a['wall-color'], 0xd8dae0)
        : undefined,
    theme: a.theme === 'holo' ? 'holo' : 'asphalt',
  });

  if (nodes.length >= 3) {
    const spline = setTrackSpline(state, entity, nodes, {
      width,
      closed: bool(a.closed, true),
      step: a.step !== undefined ? num(a.step, 2) : 2,
      maxAutoBank: a['max-bank'] !== undefined ? num(a['max-bank'], 12) : 12,
    });
    Track.length[entity] = spline.length;

    // A layout whose centerlines never cross can still overlap once the road
    // has width — two arms 12 m apart on a 16 m track share 4 m of tarmac.
    // That is always an authoring bug (z-fighting road, barriers in the racing
    // line), so say exactly where it happens instead of shipping it silently.
    const shoulder = Track.shoulder[entity] || 0;
    const overlaps = spline.selfOverlaps(5, Math.max(40, width * 3));
    if (overlaps.length > 0) {
      const worst = overlaps.reduce((a1, b1) => (a1.gap < b1.gap ? a1 : b1));
      logger.warn(
        `[Racing] track "${String(a.name ?? 'circuit')}" overlaps itself at ` +
          `${overlaps.length} place(s): the corridors at ${worst.aS.toFixed(0)} m and ` +
          `${worst.bS.toFixed(0)} m are only ${worst.gap.toFixed(1)} m apart ` +
          `(need ${(width + shoulder * 2).toFixed(1)} m, or ≥5 m of height for a flyover). ` +
          `Move those control points apart or narrow the road there.`
      );
    }
  }
};

/**
 * `<RaceTrackPickup s="80" lateral="0" kind="pulse" />` — a power-up orb.
 *
 * `s` and `lateral` are in track space; the orb spawns when the track spline
 * exists (first frame after parse). Kind: pulse | sidewinder | shield.
 */
const raceTrackPickupParser: Parser = ({ state, entity, element }) => {
  const a = element.attributes;
  const s = num(a.s, 0);
  const lateral = a.lateral !== undefined ? num(a.lateral, 0) : 0;
  const kindText = String(a.kind ?? 'pulse').toLowerCase();
  const kind = kindText === 'shield' ? 2 : kindText === 'sidewinder' ? 1 : 0;
  const respawn = a.respawn !== undefined ? num(a.respawn, 6) : 6;
  // Register the pickup in the shared sidecar; the visual system creates the
  // entity once the track spline is attached.
  addTrackPickup(s, lateral, kind, respawn);
  void state;
  void entity;
};

/**
 * `<RaceTrackObstacle s="120" lateral="-2" radius="1.4" kind="barrel" />`.
 *
 * A solid track-side obstacle. `kind`: barrel | drone | gate — affects only
 * the visual; the physics is the same circle.
 */
const raceTrackObstacleParser: Parser = ({ state, entity, element }) => {
  const a = element.attributes;
  const s = num(a.s, 0);
  const lateral = a.lateral !== undefined ? num(a.lateral, 0) : 0;
  const radius = a.radius !== undefined ? num(a.radius, 1.2) : 1.2;
  const bounce = a.bounce !== undefined ? num(a.bounce, 0.4) : 0.4;
  const kindText = String(a.kind ?? 'barrel').toLowerCase();
  const kind =
    kindText === 'gate' ? 2 : kindText === 'drone' ? 1 : 0;
  // Resolve the world position from the track spline (first track wins).
  const trackEid = getPrimaryTrackEntity();
  if (trackEid === undefined) return;
  const spline = getTrackSpline(trackEid);
  if (!spline) return;
  const f = spline.positionAt(s, lateral);
  addTrackObstacle(f.x, f.z, radius, bounce);
  addTrackObstacleByS(s, lateral, radius, bounce, kind);
  // Attach the TrackObstacleState component for the visual system.
  state.addComponent(entity, TrackObstacleState);
  TrackObstacleState.s[entity] = s;
  TrackObstacleState.lateral[entity] = lateral;
  TrackObstacleState.radius[entity] = radius;
  TrackObstacleState.bounce[entity] = bounce;
  TrackObstacleState.kind[entity] = kind;
  TrackObstacleState.spin[entity] = kind === 0 ? 2 : kind === 1 ? 1.2 : 0;
  TrackObstacleState.hover[entity] = kind === 1 ? 1.1 : 0;
};

const chaseCameraParser: Parser = ({ entity, element }) => {
  const a = element.attributes;
  if (a.target !== undefined && a.target !== '') {
    pendingCameraTargets.set(entity, String(a.target));
  }
  if (a.distance !== undefined)
    ChaseCamera.distance[entity] = num(a.distance, 7.5);
  if (a.height !== undefined) ChaseCamera.height[entity] = num(a.height, 3);
  if (a['follow-lag'] !== undefined)
    ChaseCamera.followLag[entity] = num(a['follow-lag'], 0.12);
  if (a['turn-lag'] !== undefined)
    ChaseCamera.turnLag[entity] = num(a['turn-lag'], 0.16);
  if (a['look-ahead'] !== undefined)
    ChaseCamera.lookAhead[entity] = num(a['look-ahead'], 4);
  if (a.fov !== undefined) ChaseCamera.fovBase[entity] = num(a.fov, 72);
  if (a['fov-boost'] !== undefined)
    ChaseCamera.fovBoost[entity] = num(a['fov-boost'], 12);
  if (a.mode !== undefined)
    ChaseCamera.mode[entity] = Math.max(0, Math.floor(num(a.mode)));
};

// ---- Plugin ----------------------------------------------------------------

/**
 * Arcade kart racing: circuits, vehicles, rivals, the race itself and its HUD.
 *
 * System order per frame is `AiDriver → VehicleControl` (both fixed step), then
 * `RaceDirector → TrackSpawn` in simulation, then the draw-time visuals.
 */
export const RacingPlugin: Plugin = {
  systems: [
    AiDriverSystem,
    PowerUpSystem,
    VehicleControlSystem,
    RaceDirectorSystem,
    ChaseCameraBindSystem,
    TrackSpawnSystem,
    CheckpointSystem,
    PickupSystem,
    VehicleVisualSystem,
    ChaseCameraSystem,
    VehicleFxSystem,
    HoloPulseSystem,
    PickupVisualSystem,
    TrackObstacleVisualSystem,
    EngineAudioSystem,
  ],
  recipes: [
    vehicleRecipe,
    playerVehicleRecipe,
    aiVehicleRecipe,
    trackRecipe,
    chaseCameraRecipe,
    raceTrackPickupRecipe,
    raceTrackObstacleRecipe,
  ],
  components: {
    vehicle: Vehicle,
    'player-vehicle': PlayerVehicle,
    'ai-driver': AiDriver,
    track: Track,
    'race-tracker': RaceTracker,
    'chase-camera': ChaseCamera,
    'power-up': PowerUp,
    'pickup-orb': PickupOrb,
    'track-obstacle': TrackObstacleState,
  },
  config: {
    defaults: {
      vehicle: {
        maxSpeed: 46,
        accel: 26,
        brake: 48,
        engineBrake: 7,
        reverseSpeed: 12,
        maxSteer: 2.6,
        steerSpeed: 10,
        grip: 7,
        driftGrip: 0.32,
        boostAccel: 16,
        boostSpeed: 1.3,
        boostCapacity: 0,
        boostRecharge: 0.22,
        halfLength: 1.35,
        halfWidth: 0.85,
        rideHeight: 0.35,
        throttle: 0,
        brakeInput: 0,
        steerInput: 0,
        handbrake: 0,
        boostInput: 0,
        speed: 0,
        lateralSpeed: 0,
        heading: 0,
        yawRate: 0,
        steer: 0,
        trackS: 0,
        trackLateral: 0,
        airHeight: 0.35,
        verticalSpeed: 0,
        airborne: 0,
        surfaceGrip: 1,
        slip: 0,
        boost: 0,
        boosting: 0,
        impactTimer: 10,
        rpm: 0,
        gear: 1,
        wheelSpin: 0,
        wheelSteer: 0,
        roll: 0,
        pitch: 0,
      },
      'ai-driver': {
        skill: 0.82,
        lineOffset: 0,
        rubberBand: 0.5,
        steerState: 0,
        noisePhase: 0,
        stuckTimer: 0,
        progressS: 0,
      },
      'race-tracker': {
        track: 0,
        lap: 0,
        lastS: 0,
        distance: 0,
        lapStartTime: 0,
        bestLapTime: -1,
        lastLapTime: -1,
        finished: 0,
        finishTime: -1,
        position: 1,
        wrongWay: 0,
        wrongWayTimer: 0,
        gridSlot: 0,
      },
      track: {
        totalLaps: 3,
        length: 0,
        width: 12,
        shoulder: 3,
        walls: 1,
      },
      'chase-camera': {
        target: 0,
        distance: 7.5,
        height: 3,
        followLag: 0.12,
        turnLag: 0.16,
        lookAhead: 4,
        fovBase: 72,
        fovBoost: 12,
        mode: 0,
        followX: 0,
        followY: 0,
        followZ: 0,
        smoothYaw: 0,
        upX: 0,
        upY: 1,
        upZ: 0,
        fov: 72,
        orbitAngle: 0,
        initialized: 0,
      },
      'power-up': {
        ammo0: 0,
        ammo1: 0,
        ammo2: 0,
        cap0: 0,
        cap1: 0,
        cap2: 0,
        cd0: 0,
        cd1: 0,
        cd2: 0,
        cdTotal0: 0,
        cdTotal1: 0,
        cdTotal2: 0,
        shieldArmed: 0,
        pulseBoost: 0,
      },
      'track-obstacle': {
        s: 0,
        lateral: 0,
        radius: 1.2,
        bounce: 0.4,
        kind: 0,
        spin: 0,
        hover: 0,
      },
    },
    parsers: {
      Vehicle: vehicleParser,
      PlayerVehicle: vehicleParser,
      AiVehicle: vehicleParser,
      RaceTrack: trackParser,
      ChaseCamera: chaseCameraParser,
      RaceTrackPickup: raceTrackPickupParser,
      RaceTrackObstacle: raceTrackObstacleParser,
    },
  },
  initialize() {
    registerRacingHudFactories();
  },
};
