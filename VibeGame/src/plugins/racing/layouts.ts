import { defineSystem, defineQuery, type State, type System } from '../../core';
import { ItemBox, Track, TrackObstacleState } from './components';
import {
  addItemBox,
  addTrackObstacle,
  addTrackObstacleByS,
  clearItemBoxes,
  getItemBoxes,
  getTrackRamps,
  getTrackSpline,
  getTrackSpaceObstacles,
  removeTrackObstacles,
  removeTrackSpaceObstacles,
  setWorldObstacleTrackIdx,
  type TrackSpline,
} from './data';
import { getRaceState } from './race-state';
import { ObstacleKind, ObstacleMoveMode } from './components';

/**
 * Procedural hazard layouts: item-box rows and obstacle picks generated from a
 * seed, re-rolled every race generation when the seed mode is `auto` and
 * frozen when it is `fixed` (time-trial keeps the layout stable so laps — and
 * ghosts — stay comparable).
 *
 * The generators are pure given a random source, so a seed reproduces a layout
 * exactly and the tests can verify placement rules without a scene.
 */

/** Deterministic 32-bit PRNG (mulberry32). */
export function mulberry32(seed: number): () => number {
  let a = seed | 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A generated chest position. */
export interface BoxPlacement {
  s: number;
  lateral: number;
}

/** A generated obstacle, before it becomes entity + sidecars. */
export interface ObstacleSpec {
  s: number;
  lateral: number;
  radius: number;
  bounce: number;
  kind: number;
  moveMode: number;
  moveSpeed: number;
  moveRange: number;
  breakable: number;
}

export interface HazardsLayoutOptions {
  /** `auto` re-rolls every race; `fixed` always reuses `seed`. */
  seedMode: 'auto' | 'fixed';
  seed: number;
  /** Item-box rows around the lap. */
  rows: number;
  /** Chests across the road per row. */
  perRow: number;
  /** Parked obstacles (barrels, gates, drones). */
  obstacles: number;
  /** Moving obstacles (sweeping drones, rolling barrels). */
  moving: number;
  /** Breakable crates. */
  crates: number;
}

const DEFAULTS: HazardsLayoutOptions = {
  seedMode: 'auto',
  seed: 1,
  rows: 6,
  perRow: 3,
  obstacles: 4,
  moving: 3,
  crates: 2,
};

let layoutConfig: HazardsLayoutOptions | null = null;
let appliedGeneration = -1;
let createdObstacleEids: number[] = [];

export function setHazardsLayout(
  options: Partial<HazardsLayoutOptions>
): HazardsLayoutOptions {
  layoutConfig = { ...DEFAULTS, ...options };
  return layoutConfig;
}

export function getHazardsLayout(): HazardsLayoutOptions | null {
  return layoutConfig;
}

export function clearHazardsLayout(): void {
  layoutConfig = null;
  appliedGeneration = -1;
  createdObstacleEids = [];
}

// ---- Placement rules ---------------------------------------------------------

/** Arc clearance kept clear around the start/finish line. */
const START_CLEAR_S = 120;
/** A candidate needs curvature below this on itself ±15 m (1/m). */
const MAX_CURVATURE = 0.008;
/** Minimum spacing between item rows / obstacles (m). */
const ROW_GAP = 300;
const OBSTACLE_GAP = 140;
/** Cross-clearance between boxes and obstacles (m). */
const CROSS_GAP = 40;
/** Ramp spans block hazards from `s - before` to `s + length + after`. */
const RAMP_BEFORE = 25;
const RAMP_AFTER = 45;

function curvatureClear(spline: TrackSpline, s: number): boolean {
  return (
    Math.abs(spline.curvatureAt(s)) < MAX_CURVATURE &&
    Math.abs(spline.curvatureAt(s - 15)) < MAX_CURVATURE * 1.5 &&
    Math.abs(spline.curvatureAt(s + 15)) < MAX_CURVATURE * 1.5
  );
}

function rampBlocked(s: number): boolean {
  for (const r of getTrackRamps()) {
    if (s > r.s - RAMP_BEFORE && s < r.s + r.length + RAMP_AFTER) return true;
  }
  return false;
}

/** Does `s` sit within `gap` metres of any taken position (wrap-aware)? */
function nearTaken(
  spline: TrackSpline,
  s: number,
  taken: number[],
  gap: number
): boolean {
  for (const t of taken) {
    if (Math.abs(spline.deltaS(s, t)) < gap) return true;
  }
  return false;
}

/**
 * Generate item-box rows. Each row spans the road at a straight, ramp-free
 * stretch; returns one placement per chest.
 */
export function generateItemBoxRows(
  spline: TrackSpline,
  rand: () => number,
  rows: number,
  perRow: number
): BoxPlacement[] {
  const out: BoxPlacement[] = [];
  const taken: number[] = [];
  const len = spline.length;
  let attempts = 0;
  while (out.length < rows * perRow && attempts < rows * 40) {
    attempts++;
    const s = rand() * len;
    if (s < START_CLEAR_S || s > len - START_CLEAR_S) continue;
    if (!curvatureClear(spline, s)) continue;
    if (rampBlocked(s)) continue;
    if (nearTaken(spline, s, taken, ROW_GAP)) continue;
    taken.push(s);
    const width = spline.sampleAt(s).width || 12;
    const usable = Math.max(2, width * 0.5 - 1.6);
    for (let i = 0; i < perRow; i++) {
      const lateral =
        perRow === 1 ? 0 : -usable + (2 * usable * i) / (perRow - 1);
      out.push({ s, lateral });
    }
  }
  return out;
}

/** Generate the obstacle set (parked, moving and crates) avoiding boxes. */
export function generateObstacles(
  spline: TrackSpline,
  rand: () => number,
  options: Pick<HazardsLayoutOptions, 'obstacles' | 'moving' | 'crates'>,
  boxRows: number[]
): ObstacleSpec[] {
  const specs: ObstacleSpec[] = [];
  const taken: number[] = [...boxRows];
  const len = spline.length;
  const total = options.obstacles + options.moving + options.crates;
  let attempts = 0;
  while (specs.length < total && attempts < total * 50) {
    attempts++;
    const s = rand() * len;
    if (s < START_CLEAR_S || s > len - START_CLEAR_S) continue;
    if (!curvatureClear(spline, s)) continue;
    if (rampBlocked(s)) continue;
    if (nearTaken(spline, s, taken, OBSTACLE_GAP)) continue;
    if (nearTaken(spline, s, boxRows, CROSS_GAP)) continue;
    taken.push(s);
    const width = spline.sampleAt(s).width || 12;
    const usable = Math.max(2, width * 0.5 - 2);

    const made = specs.length;
    if (made < options.obstacles) {
      // Parked hazards: mostly barrels, sometimes a gate on the kerb.
      const kind = rand() < 0.75 ? ObstacleKind.Barrel : ObstacleKind.Gate;
      const lateral =
        kind === ObstacleKind.Gate
          ? (rand() < 0.5 ? -1 : 1) * usable * 0.8
          : (rand() * 2 - 1) * usable * 0.7;
      specs.push({
        s,
        lateral,
        radius: kind === ObstacleKind.Gate ? 1.1 : 0.85,
        bounce: 0.4,
        kind,
        moveMode: ObstacleMoveMode.Static,
        moveSpeed: 0,
        moveRange: 0,
        breakable: 0,
      });
    } else if (made < options.obstacles + options.moving) {
      // Moving hazards: drones sweep across the road, barrels roll on down it.
      const sweep = rand() < 0.6;
      const lateral = sweep ? 0 : (rand() * 2 - 1) * usable * 0.5;
      const range = sweep ? Math.min(usable, 3 + rand() * 2.5) : 0;
      specs.push({
        s,
        lateral,
        radius: sweep ? 1.0 : 0.85,
        bounce: 0.45,
        kind: sweep ? ObstacleKind.Drone : ObstacleKind.Barrel,
        moveMode: sweep ? ObstacleMoveMode.Sweep : ObstacleMoveMode.Travel,
        moveSpeed: sweep ? 1.2 + rand() * 1.0 : 6 + rand() * 3,
        moveRange: range,
        breakable: 0,
      });
    } else {
      // Crates: dead centre of the road, begging to be smashed.
      specs.push({
        s,
        lateral: (rand() * 2 - 1) * usable * 0.4,
        radius: 0.95,
        bounce: 0.85,
        kind: ObstacleKind.Crate,
        moveMode: ObstacleMoveMode.Static,
        moveSpeed: 0,
        moveRange: 0,
        breakable: 1,
      });
    }
  }
  return specs;
}

// ---- The applier -------------------------------------------------------------

const trackQuery = defineQuery([Track]);
const boxEntityQuery = defineQuery([ItemBox]);

function applyHazards(
  state: State,
  spline: TrackSpline,
  options: HazardsLayoutOptions
): void {
  // Tear down the previous generated set (entities, sidecars, world obstacles).
  if (createdObstacleEids.length > 0) {
    const eids = new Set(createdObstacleEids);
    const worldIdx: number[] = [];
    for (const o of getTrackSpaceObstacles()) {
      if (o.eid >= 0 && eids.has(o.eid) && o.worldIndex >= 0) {
        worldIdx.push(o.worldIndex);
      }
    }
    removeTrackObstacles(worldIdx);
    removeTrackSpaceObstacles(eids);
    for (const eid of createdObstacleEids) state.destroyEntity(eid);
    createdObstacleEids = [];
  }
  for (const def of getItemBoxes()) {
    if (def.eid >= 0) state.destroyEntity(def.eid);
  }
  clearItemBoxes();

  const rand = mulberry32(options.seed);
  const boxes = generateItemBoxRows(spline, rand, options.rows, options.perRow);
  const rowS: number[] = [];
  for (const b of boxes) {
    addItemBox(b.s, b.lateral, 5);
    if (!rowS.includes(b.s)) rowS.push(b.s);
  }
  const specs = generateObstacles(spline, rand, options, rowS);
  for (const spec of specs) {
    const p = spline.positionAt(spec.s, spec.lateral);
    const eid = state.createEntity();
    state.addComponent(eid, TrackObstacleState);
    TrackObstacleState.s[eid] = spec.s;
    TrackObstacleState.lateral[eid] = spec.lateral;
    TrackObstacleState.radius[eid] = spec.radius;
    TrackObstacleState.bounce[eid] = spec.bounce;
    TrackObstacleState.kind[eid] = spec.kind;
    TrackObstacleState.spin[eid] =
      spec.kind === ObstacleKind.Barrel
        ? 2
        : spec.kind === ObstacleKind.Drone
          ? 1.2
          : 0;
    TrackObstacleState.hover[eid] = spec.kind === ObstacleKind.Drone ? 1.1 : 0;
    TrackObstacleState.moveMode[eid] = spec.moveMode;
    TrackObstacleState.moveSpeed[eid] = spec.moveSpeed;
    TrackObstacleState.moveRange[eid] = spec.moveRange;
    TrackObstacleState.baseS[eid] = spec.s;
    TrackObstacleState.baseLateral[eid] = spec.lateral;
    TrackObstacleState.breakable[eid] = spec.breakable;
    TrackObstacleState.cooldown[eid] = 0;

    const worldIndex = addTrackObstacle(
      p.x,
      p.z,
      spec.radius,
      spec.bounce,
      spec.breakable,
      -1
    );
    const trackIdx = addTrackObstacleByS(
      spec.s,
      spec.lateral,
      spec.radius,
      spec.bounce,
      spec.kind,
      eid,
      worldIndex,
      {
        moveMode: spec.moveMode,
        moveSpeed: spec.moveSpeed,
        moveRange: spec.moveRange,
        movePhase: 0,
      }
    );
    setWorldObstacleTrackIdx(worldIndex, trackIdx);
    createdObstacleEids.push(eid);
  }
}

/**
 * Applies (and re-applies) the configured layout. Runs in `simulation` so the
 * boxes exist before the first racing frame; a race restart (`generation`
 * bump) re-rolls `auto` seeds and re-applies `fixed` ones identically.
 */
export const HazardsLayoutSystem: System = defineSystem({
  name: 'HazardsLayoutSystem',
  group: 'simulation',

  update(state: State) {
    if (!layoutConfig) return;
    const trackEid = trackQuery(state.world)[0];
    if (trackEid === undefined) return;
    const spline = getTrackSpline(trackEid);
    if (!spline) return;
    const generation = getRaceState().generation;
    // A world hot-reload wipes entities but not the sidecars: a non-empty box
    // list with zero live entities means the layout needs re-applying even
    // though the race generation never moved.
    const worldReloaded =
      getItemBoxes().some((b) => b.eid >= 0) &&
      boxEntityQuery(state.world).length === 0;
    if (appliedGeneration === generation && !worldReloaded) return;
    appliedGeneration = generation;
    const seed =
      layoutConfig.seedMode === 'auto'
        ? (Math.random() * 0x7fffffff) | 0
        : layoutConfig.seed;
    applyHazards(state, spline, { ...layoutConfig, seed });
  },

  dispose() {
    createdObstacleEids = [];
    appliedGeneration = -1;
  },
});
