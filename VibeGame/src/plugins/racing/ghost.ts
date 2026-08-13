import { defineSystem, defineQuery, type State, type System } from '../../core';
import { PlayerVehicle, RaceTracker, Vehicle } from './components';
import { createFrame, type TrackSpline } from './spline';
import { getRaceState } from './race-state';

const playerQuery = defineQuery([PlayerVehicle, Vehicle, RaceTracker]);

/** Samples per second while the player is racing. */
const SAMPLE_HZ = 12;
/** Ignore laps shorter than this — a glitch, not a record. */
const MIN_LAP_S = 5;
const MAX_SAMPLES = 2500;

const _frame = createFrame();

/** One pose on a recorded lap. `u` is metres driven since that lap started. */
export interface GhostSample {
  t: number;
  s: number;
  u: number;
  lateral: number;
  heading: number;
}

/** A personal-best lap the ghost can replay. */
export interface GhostLap {
  duration: number;
  samples: GhostSample[];
}

export interface GhostWorldPose {
  x: number;
  y: number;
  z: number;
  qx: number;
  qy: number;
  qz: number;
  qw: number;
  s: number;
  lateral: number;
}

let ghost: GhostLap | null = null;
let recording: GhostSample[] = [];
let lastSampleAt = -1;
let lastLap = -1;
let lastGeneration = -1;
let lapStartDistance = 0;

/** Metres driven since the current lap started — same `u` the ghost records. */
export function ghostProgressU(eid: number): number {
  return Math.max(0, RaceTracker.distance[eid] - lapStartDistance);
}

/** Sectors per lap for split flashes (same count the example waypoint arrow uses). */
export const GHOST_SECTOR_COUNT = 8;

export function sectorIndex(u: number, lapLength: number): number {
  if (lapLength <= 1e-3) return 0;
  const t = ((u % lapLength) + lapLength) % lapLength;
  return Math.min(
    GHOST_SECTOR_COUNT - 1,
    Math.floor((t / lapLength) * GHOST_SECTOR_COUNT)
  );
}

/** Arc metres of a completed-sector boundary (sector 1 → L/8). */
export function sectorBoundaryU(sector: number, lapLength: number): number {
  return (sector / GHOST_SECTOR_COUNT) * lapLength;
}

/**
 * Sector just completed (1 … count-1) when `u` crossed a boundary.
 * Lap wrap (u drops) returns null — the lap time already covers sector 8.
 */
export function completedSector(
  prevU: number,
  nextU: number,
  lapLength: number
): number | null {
  if (lapLength <= 1e-3) return null;
  if (nextU + 1 < prevU) return null;
  const a = sectorIndex(prevU, lapLength);
  const b = sectorIndex(nextU, lapLength);
  if (b > a) return b;
  return null;
}

/** Currently stored personal-best ghost, or `null` until a lap is committed. */
export function getGhostLap(): GhostLap | null {
  return ghost;
}

/** Install a ghost (save-load, tests). Invalid payloads become `null`. */
export function setGhostLap(lap: GhostLap | null): void {
  ghost = sanitizeLap(lap);
}

export function clearGhost(): void {
  ghost = null;
  recording = [];
  lastSampleAt = -1;
  lastLap = -1;
  lastGeneration = -1;
  lapStartDistance = 0;
}

/** Reset per-race caches without dropping the stored PB ghost. */
export function resetGhostRecording(): void {
  recording = [];
  lastSampleAt = -1;
  lastLap = -1;
  lastGeneration = -1;
  lapStartDistance = 0;
}

/**
 * Pose of the ghost at `lapTime` seconds into the current lap.
 * `null` when there is no ghost, or before the first sample.
 */
export function sampleGhostAtTime(lapTime: number): GhostSample | null {
  if (!ghost || ghost.samples.length === 0) return null;
  return interpolateByT(ghost.samples, Math.max(0, lapTime));
}

/**
 * Live delta (s): player lap time minus the ghost's time at the same distance.
 * Negative = ahead of the ghost. `null` when there is no ghost yet.
 */
export function ghostDeltaAt(u: number, lapTime: number): number | null {
  if (!ghost || ghost.samples.length < 2) return null;
  const ghostT = timeAtU(ghost.samples, Math.max(0, u));
  if (ghostT === null) return null;
  return lapTime - ghostT;
}

export function serializeGhostLap(lap: GhostLap | null): unknown {
  if (!lap || lap.samples.length === 0) return null;
  return {
    duration: lap.duration,
    samples: lap.samples.map((s) => [s.t, s.s, s.u, s.lateral, s.heading]),
  };
}

export function parseGhostLap(data: unknown): GhostLap | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as { duration?: unknown; samples?: unknown };
  if (typeof d.duration !== 'number' || !Array.isArray(d.samples)) return null;
  const samples: GhostSample[] = [];
  for (const row of d.samples) {
    if (!Array.isArray(row) || row.length < 5) continue;
    const t = Number(row[0]);
    const s = Number(row[1]);
    const u = Number(row[2]);
    const lateral = Number(row[3]);
    const heading = Number(row[4]);
    if (![t, s, u, lateral, heading].every(Number.isFinite)) continue;
    samples.push({ t, s, u, lateral, heading });
  }
  return sanitizeLap({ duration: d.duration, samples });
}

/**
 * World pose of a ghost sample on a spline — same basis as a live car, so the
 * hologram sits on the road and leans with the banking.
 */
export function ghostWorldPose(
  spline: TrackSpline,
  sample: GhostSample,
  rideHeight = 0.35
): GhostWorldPose {
  const f = spline.sampleAt(sample.s, _frame);
  const pos = spline.positionAt(sample.s, sample.lateral, rideHeight);
  const q = quatFromHeadingAndUp(sample.heading, f.ux, f.uy, f.uz);
  return {
    x: pos.x,
    y: pos.y,
    z: pos.z,
    qx: q.x,
    qy: q.y,
    qz: q.z,
    qw: q.w,
    s: sample.s,
    lateral: sample.lateral,
  };
}

/**
 * Records the player's lap and commits it as the ghost whenever it beats the
 * stored PB. Playback is time-based (the hologram runs the same clock as the
 * current lap); the HUD delta is distance-based (time at the same `u`).
 */
export const GhostSystem: System = defineSystem({
  name: 'GhostSystem',
  group: 'simulation',
  after: ['RaceDirectorSystem'],

  update(state: State) {
    const race = getRaceState();
    const player = playerQuery(state.world)[0];
    if (player === undefined) return;

    if (race.generation !== lastGeneration) {
      lastGeneration = race.generation;
      recording = [];
      lastSampleAt = -1;
      lastLap = RaceTracker.lap[player];
      lapStartDistance = RaceTracker.distance[player];
    }

    if (race.phase !== 'racing' && race.phase !== 'finished') return;

    const lap = RaceTracker.lap[player];
    if (lap > lastLap) {
      const lapTime = RaceTracker.lastLapTime[player];
      if (lapTime >= MIN_LAP_S && recording.length >= 8) {
        if (!ghost || lapTime < ghost.duration - 0.01) {
          ghost = { duration: lapTime, samples: recording.slice() };
        }
      }
      recording = [];
      lastSampleAt = -1;
      lastLap = lap;
      lapStartDistance = RaceTracker.distance[player];
    }

    if (race.phase !== 'racing' || RaceTracker.finished[player]) return;

    const lapTime = race.raceTime - RaceTracker.lapStartTime[player];
    if (lapTime - lastSampleAt < 1 / SAMPLE_HZ) return;
    lastSampleAt = lapTime;
    if (recording.length >= MAX_SAMPLES) return;
    recording.push({
      t: lapTime,
      s: Vehicle.trackS[player],
      u: Math.max(0, RaceTracker.distance[player] - lapStartDistance),
      lateral: Vehicle.trackLateral[player],
      heading: Vehicle.heading[player],
    });
  },

  dispose() {
    resetGhostRecording();
  },
});

function sanitizeLap(lap: GhostLap | null): GhostLap | null {
  if (!lap || lap.samples.length < 2) return null;
  if (!Number.isFinite(lap.duration) || lap.duration < MIN_LAP_S) return null;
  const samples = lap.samples
    .filter((s) => Number.isFinite(s.t) && Number.isFinite(s.u))
    .sort((a, b) => a.t - b.t)
    .slice(0, MAX_SAMPLES);
  if (samples.length < 2) return null;
  return { duration: lap.duration, samples };
}

function interpolateByT(
  samples: readonly GhostSample[],
  t: number
): GhostSample {
  const last = samples[samples.length - 1]!;
  if (t <= samples[0]!.t) return samples[0]!;
  if (t >= last.t) return last;
  const i = upperBoundT(samples, t);
  const a = samples[i - 1]!;
  const b = samples[i]!;
  const span = b.t - a.t;
  const k = span > 1e-6 ? (t - a.t) / span : 0;
  return lerpSample(a, b, k);
}

function timeAtU(samples: readonly GhostSample[], u: number): number | null {
  const first = samples[0]!;
  const last = samples[samples.length - 1]!;
  if (u <= first.u) return first.t;
  if (u >= last.u) return last.t;
  let lo = 0;
  let hi = samples.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid]!.u < u) lo = mid + 1;
    else hi = mid;
  }
  const i = Math.max(1, lo);
  const a = samples[i - 1]!;
  const b = samples[i]!;
  const span = b.u - a.u;
  const k = span > 1e-6 ? (u - a.u) / span : 0;
  return a.t + (b.t - a.t) * k;
}

function upperBoundT(samples: readonly GhostSample[], t: number): number {
  let lo = 0;
  let hi = samples.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (samples[mid]!.t <= t) lo = mid + 1;
    else hi = mid;
  }
  return Math.min(samples.length - 1, Math.max(1, lo));
}

function lerpSample(a: GhostSample, b: GhostSample, k: number): GhostSample {
  return {
    t: a.t + (b.t - a.t) * k,
    s: wrapLerpS(a.s, b.s, k),
    u: a.u + (b.u - a.u) * k,
    lateral: a.lateral + (b.lateral - a.lateral) * k,
    heading: lerpAngle(a.heading, b.heading, k),
  };
}

function wrapLerpS(a: number, b: number, k: number): number {
  // Consecutive samples are a few metres apart; a huge backwards jump is the
  // start/finish wrap. Snap rather than lerp across the whole circuit.
  if (b + 80 < a) return k < 0.5 ? a : b;
  return a + (b - a) * k;
}

function lerpAngle(a: number, b: number, k: number): number {
  let d = b - a;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}

/** Same basis as `writeOrientation` in vehicle-control — yaw around the road up. */
export function quatFromHeadingAndUp(
  heading: number,
  ux: number,
  uy: number,
  uz: number
): { x: number; y: number; z: number; w: number } {
  let fx = Math.sin(heading);
  let fy = 0;
  let fz = Math.cos(heading);
  const d = fx * ux + fy * uy + fz * uz;
  fx -= ux * d;
  fy -= uy * d;
  fz -= uz * d;
  const fl = Math.hypot(fx, fy, fz) || 1;
  fx /= fl;
  fy /= fl;
  fz /= fl;
  const rx = uy * fz - uz * fy;
  const ry = uz * fx - ux * fz;
  const rz = ux * fy - uy * fx;
  const m00 = rx;
  const m10 = ry;
  const m20 = rz;
  const m01 = ux;
  const m11 = uy;
  const m21 = uz;
  const m02 = fx;
  const m12 = fy;
  const m22 = fz;
  const trace = m00 + m11 + m22;
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1);
    return {
      w: 0.25 / s,
      x: (m21 - m12) * s,
      y: (m02 - m20) * s,
      z: (m10 - m01) * s,
    };
  }
  if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    return {
      w: (m21 - m12) / s,
      x: 0.25 * s,
      y: (m01 + m10) / s,
      z: (m02 + m20) / s,
    };
  }
  if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    return {
      w: (m02 - m20) / s,
      x: (m01 + m10) / s,
      y: 0.25 * s,
      z: (m12 + m21) / s,
    };
  }
  const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
  return {
    w: (m10 - m01) / s,
    x: (m02 + m20) / s,
    y: (m12 + m21) / s,
    z: 0.25 * s,
  };
}
