import { flattenNumberList } from '../../core';
import type { Parser, Plugin, Recipe, XMLValue } from '../../core';
import {
  AiDriver,
  ChaseCamera,
  HeldItem,
  ItemBox,
  ItemKind,
  ObstacleKind,
  ObstacleMoveMode,
  PlayerVehicle,
  RaceTracker,
  Track,
  TrackObstacleState,
  Vehicle,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
} from './components';
import {
  setTrackSpline,
  addTrackRamp,
  addItemBox,
  addTrackObstacleByS,
  addTrackObstacle,
  setWorldObstacleTrackIdx,
  getTrackSpline,
  getPrimaryTrackEntity,
} from './data';
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
import { StartLightsSystem } from './start-lights';
import { RaceConditionsSystem } from './conditions';
import { VehicleControlSystem } from './vehicle-control';
import { VehicleFxSystem } from './vehicle-fx';
import { VehicleVisualSystem } from './vehicle-visual';
import { registerRacingHudFactories } from './hud';
import { ItemSystem } from './items';
import { ItemBoxSystem, ItemBoxVisualSystem } from './item-boxes';
import { TrickSystem } from './tricks';
import { RampVisualSystem } from './ramps';
import { RacingFxSystem } from './fx-events';
import {
  HazardsLayoutSystem,
  setHazardsLayout,
  type HazardsLayoutOptions,
} from './layouts';
import { MovingObstacleSystem, TrackObstacleVisualSystem } from './obstacles';
import { GhostSystem } from './ghost';
import { GhostVisualSystem } from './ghost-visual';
import { CheckpointSystem } from './checkpoints';
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
  'start-item',
] as const;

/**
 * `<Vehicle>` — an arcade car. Deliberately has **no rigidbody**: the racing
 * plugin owns vehicle motion (see {@link VehicleControlSystem}), which is what
 * makes grounding, banking and barriers behave.
 */
export const vehicleRecipe: Recipe = {
  name: 'Vehicle',
  components: ['transform', 'vehicle', 'race-tracker', 'held-item'],
  parserAttributes: [...VEHICLE_ATTRS],
};

/** `<PlayerVehicle>` — the car the local player drives. */
export const playerVehicleRecipe: Recipe = {
  name: 'PlayerVehicle',
  components: [
    'transform',
    'vehicle',
    'player-vehicle',
    'race-tracker',
    'held-item',
  ],
  parserAttributes: [...VEHICLE_ATTRS],
};

/** `<AiVehicle skill="0.9" rubber-band="0.6">` — a computer-driven rival. */
export const aiVehicleRecipe: Recipe = {
  name: 'AiVehicle',
  components: [
    'transform',
    'vehicle',
    'ai-driver',
    'race-tracker',
    'held-item',
  ],
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
    'viaduct-clearance',
    'pylon-spacing',
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

/**
 * `<RaceTrackItemBox s="80" lateral="0" respawn="5" />` — a single item chest.
 * Layout generators place these in batches; the tag exists for hand-tuning.
 */
export const raceTrackItemBoxRecipe: Recipe = {
  name: 'RaceTrackItemBox',
  components: [],
  parserAttributes: ['s', 'lateral', 'respawn'],
};

/**
 * `<RaceTrackRamp s="300" length="12" width="7" height="2.4" lateral="0" />` —
 * a jump wedge: grounded cars climb the linear profile and launch off the lip.
 */
export const raceTrackRampRecipe: Recipe = {
  name: 'RaceTrackRamp',
  components: [],
  parserAttributes: ['s', 'lateral', 'length', 'width', 'height'],
};

/**
 * `<HazardsLayout seed="auto" rows="6" per-row="3" obstacles="4" moving="3"
 * crates="2" />` — generate the item boxes and obstacles procedurally.
 * `seed="auto"` re-rolls every race; a number keeps the layout fixed.
 */
export const hazardsLayoutRecipe: Recipe = {
  name: 'HazardsLayout',
  components: [],
  parserAttributes: [
    'seed',
    'rows',
    'per-row',
    'obstacles',
    'moving',
    'crates',
  ],
};

/** `<RaceTrackObstacle s="120" lateral="-2" kind="barrel" />` — a hazard. */
export const raceTrackObstacleRecipe: Recipe = {
  name: 'RaceTrackObstacle',
  components: [],
  parserAttributes: [
    's',
    'lateral',
    'radius',
    'bounce',
    'kind',
    'move',
    'move-speed',
    'move-range',
    'breakable',
  ],
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

  // Starting item: `start-item="turbo"` — handy for tests and demos.
  if (a['start-item'] !== undefined) {
    const text = String(a['start-item']).toLowerCase();
    const item =
      text === 'fireball'
        ? ItemKind.Fireball
        : text === 'oil'
          ? ItemKind.Oil
          : text === 'shield'
            ? ItemKind.Shield
            : text === 'none' || text === ''
              ? ItemKind.None
              : ItemKind.Turbo;
    HeldItem.item[entity] = item;
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
  Track.viaductClearance[entity] =
    a['viaduct-clearance'] !== undefined
      ? Math.max(0, num(a['viaduct-clearance'], 0))
      : 0;
  Track.pylonSpacing[entity] =
    a['pylon-spacing'] !== undefined
      ? Math.max(0, num(a['pylon-spacing'], 0))
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
 * `<RaceTrackItemBox s="80" lateral="0" respawn="5" />` — a single item chest
 * in track space. The contents are rolled on collection.
 */
const raceTrackItemBoxParser: Parser = ({ element }) => {
  const a = element.attributes;
  const s = num(a.s, 0);
  const lateral = a.lateral !== undefined ? num(a.lateral, 0) : 0;
  const respawn = a.respawn !== undefined ? num(a.respawn, 5) : 5;
  addItemBox(s, lateral, respawn);
};

/**
 * `<RaceTrackRamp s="300" length="12" width="7" height="2.4" />`.
 *
 * Grounded cars inside the span climb the wedge; leaving the far end converts
 * speed into a vertical launch (slope × speed).
 */
const raceTrackRampParser: Parser = ({ element }) => {
  const a = element.attributes;
  addTrackRamp(
    num(a.s, 0),
    a.length !== undefined ? num(a.length, 10) : 10,
    a.width !== undefined ? num(a.width, 6) : 6,
    a.height !== undefined ? num(a.height, 2) : 2,
    a.lateral !== undefined ? num(a.lateral, 0) : 0
  );
};

/**
 * `<HazardsLayout>` — configure the procedural generator. Applied once the
 * track spline exists and re-rolled on every race restart when `seed="auto"`.
 */
const hazardsLayoutParser: Parser = ({ element }) => {
  const a = element.attributes;
  const options: Partial<HazardsLayoutOptions> = {};
  if (a.seed !== undefined) {
    const text = String(a.seed).toLowerCase();
    options.seedMode = text === 'auto' ? 'auto' : 'fixed';
    options.seed = text === 'auto' ? 1 : num(a.seed, 1);
  }
  if (a.rows !== undefined) options.rows = Math.max(0, num(a.rows, 6));
  if (a['per-row'] !== undefined) {
    options.perRow = Math.max(1, num(a['per-row'], 3));
  }
  if (a.obstacles !== undefined) {
    options.obstacles = Math.max(0, num(a.obstacles, 4));
  }
  if (a.moving !== undefined) options.moving = Math.max(0, num(a.moving, 3));
  if (a.crates !== undefined) options.crates = Math.max(0, num(a.crates, 2));
  setHazardsLayout(options);
};

/**
 * `<RaceTrackObstacle s="120" lateral="-2" radius="1.4" kind="barrel"
 * move="sweep" move-speed="1.6" move-range="4" breakable="false" />`.
 *
 * A solid track-side obstacle. `kind`: barrel | drone | gate | crate — affects
 * only the visual (crates shatter); `move`: sweep (side to side) | travel
 * (rolls on down the track).
 */
const raceTrackObstacleParser: Parser = ({ state, entity, element }) => {
  const a = element.attributes;
  const s = num(a.s, 0);
  const lateral = a.lateral !== undefined ? num(a.lateral, 0) : 0;
  const radius = a.radius !== undefined ? num(a.radius, 1.2) : 1.2;
  const bounce = a.bounce !== undefined ? num(a.bounce, 0.4) : 0.4;
  const kindText = String(a.kind ?? 'barrel').toLowerCase();
  const kind =
    kindText === 'crate'
      ? ObstacleKind.Crate
      : kindText === 'gate'
        ? ObstacleKind.Gate
        : kindText === 'drone'
          ? ObstacleKind.Drone
          : ObstacleKind.Barrel;
  const moveText = String(a.move ?? 'static').toLowerCase();
  const moveMode =
    moveText === 'sweep'
      ? ObstacleMoveMode.Sweep
      : moveText === 'travel'
        ? ObstacleMoveMode.Travel
        : ObstacleMoveMode.Static;
  const moveSpeed =
    a['move-speed'] !== undefined
      ? num(a['move-speed'], moveMode === ObstacleMoveMode.Sweep ? 1.5 : 7)
      : moveMode === ObstacleMoveMode.Sweep
        ? 1.5
        : 7;
  const moveRange =
    a['move-range'] !== undefined ? num(a['move-range'], 3.5) : 3.5;
  const breakable = kind === ObstacleKind.Crate || bool(a.breakable, false);
  // Resolve the world position from the track spline (first track wins).
  const trackEid = getPrimaryTrackEntity();
  if (trackEid === undefined) return;
  const spline = getTrackSpline(trackEid);
  if (!spline) return;
  const f = spline.positionAt(s, lateral);
  const worldIndex = addTrackObstacle(
    f.x,
    f.z,
    radius,
    bounce,
    breakable ? 1 : 0
  );
  const trackIdx = addTrackObstacleByS(
    s,
    lateral,
    radius,
    bounce,
    kind,
    entity,
    worldIndex,
    {
      moveMode,
      moveSpeed,
      moveRange: moveMode === ObstacleMoveMode.Sweep ? moveRange : 0,
      movePhase: 0,
    }
  );
  setWorldObstacleTrackIdx(worldIndex, trackIdx);
  // Attach the TrackObstacleState component for the visual + movement systems.
  state.addComponent(entity, TrackObstacleState);
  TrackObstacleState.s[entity] = s;
  TrackObstacleState.lateral[entity] = lateral;
  TrackObstacleState.radius[entity] = radius;
  TrackObstacleState.bounce[entity] = bounce;
  TrackObstacleState.kind[entity] = kind;
  TrackObstacleState.spin[entity] =
    kind === ObstacleKind.Barrel ? 2 : kind === ObstacleKind.Drone ? 1.2 : 0;
  TrackObstacleState.hover[entity] = kind === ObstacleKind.Drone ? 1.1 : 0;
  TrackObstacleState.moveMode[entity] = moveMode;
  TrackObstacleState.moveSpeed[entity] = moveSpeed;
  TrackObstacleState.moveRange[entity] =
    moveMode === ObstacleMoveMode.Sweep ? moveRange : 0;
  TrackObstacleState.baseS[entity] = s;
  TrackObstacleState.baseLateral[entity] = lateral;
  TrackObstacleState.breakable[entity] = breakable ? 1 : 0;
  TrackObstacleState.cooldown[entity] = 0;
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
    VehicleControlSystem,
    TrickSystem,
    ItemSystem,
    MovingObstacleSystem,
    ItemBoxSystem,
    RaceDirectorSystem,
    GhostSystem,
    ChaseCameraBindSystem,
    TrackSpawnSystem,
    RaceConditionsSystem,
    StartLightsSystem,
    CheckpointSystem,
    HazardsLayoutSystem,
    VehicleVisualSystem,
    GhostVisualSystem,
    ChaseCameraSystem,
    VehicleFxSystem,
    RacingFxSystem,
    HoloPulseSystem,
    ItemBoxVisualSystem,
    TrackObstacleVisualSystem,
    RampVisualSystem,
    EngineAudioSystem,
  ],
  recipes: [
    vehicleRecipe,
    playerVehicleRecipe,
    aiVehicleRecipe,
    trackRecipe,
    chaseCameraRecipe,
    raceTrackItemBoxRecipe,
    raceTrackRampRecipe,
    hazardsLayoutRecipe,
    raceTrackObstacleRecipe,
  ],
  components: {
    vehicle: Vehicle,
    'player-vehicle': PlayerVehicle,
    'ai-driver': AiDriver,
    track: Track,
    'race-tracker': RaceTracker,
    'chase-camera': ChaseCamera,
    'held-item': HeldItem,
    'item-box': ItemBox,
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
        draft: 0,
        trickKind: 0,
        trickSpin: 0,
        trickActive: 0,
        spinOutTimer: 0,
        spinOutTotal: 0,
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
      'held-item': {
        item: 0,
        rouletteTimer: 0,
        shieldArmed: 0,
        shieldTime: 0,
        turboTime: 0,
      },
      'track-obstacle': {
        s: 0,
        lateral: 0,
        radius: 1.2,
        bounce: 0.4,
        kind: 0,
        spin: 0,
        hover: 0,
        moveMode: 0,
        moveSpeed: 0,
        moveRange: 0,
        movePhase: 0,
        baseS: 0,
        baseLateral: 0,
        breakable: 0,
        cooldown: 0,
      },
    },
    parsers: {
      Vehicle: vehicleParser,
      PlayerVehicle: vehicleParser,
      AiVehicle: vehicleParser,
      RaceTrack: trackParser,
      ChaseCamera: chaseCameraParser,
      RaceTrackItemBox: raceTrackItemBoxParser,
      RaceTrackRamp: raceTrackRampParser,
      HazardsLayout: hazardsLayoutParser,
      RaceTrackObstacle: raceTrackObstacleParser,
    },
  },
  initialize() {
    registerRacingHudFactories();
  },
};
