import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from '../../../src/plugins/racing/components';
import {
  attachTrackSpline,
  clearTrackData,
} from '../../../src/plugins/racing/data';
import { TrackSpline } from '../../../src/plugins/racing/spline';
import {
  GhostSystem,
  clearGhost,
  getGhostLap,
  ghostDeltaAt,
  parseGhostLap,
  sampleGhostAtTime,
  serializeGhostLap,
  setGhostLap,
  sectorIndex,
  sectorBoundaryU,
  completedSector,
  type GhostLap,
} from '../../../src/plugins/racing/ghost';
import {
  getRaceState,
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';

const FIXED_DT = 1 / 60;

function oval(): TrackSpline {
  return new TrackSpline(
    [
      { x: 0, y: 0, z: -200, width: 16 },
      { x: 200, y: 0, z: -200, width: 16 },
      { x: 280, y: 0, z: 0, width: 16 },
      { x: 200, y: 0, z: 200, width: 16 },
      { x: 0, y: 0, z: 200, width: 16 },
      { x: -200, y: 0, z: 200, width: 16 },
      { x: -280, y: 0, z: 0, width: 16 },
      { x: -200, y: 0, z: -200, width: 16 },
    ],
    { step: 4 }
  );
}

function makeLap(duration: number, points = 20): GhostLap {
  const samples = [];
  for (let i = 0; i < points; i++) {
    const k = i / (points - 1);
    samples.push({
      t: duration * k,
      s: 400 * k,
      u: 400 * k,
      lateral: 0,
      heading: 0,
    });
  }
  return { duration, samples };
}

afterEach(() => {
  clearGhost();
  resetRaceState();
  clearTrackData();
});

describe('ghost: playback', () => {
  it('interpolates pose by lap time', () => {
    setGhostLap(makeLap(40));
    const mid = sampleGhostAtTime(20);
    expect(mid).not.toBeNull();
    expect(mid!.u).toBeCloseTo(200, 0);
    expect(sampleGhostAtTime(0)!.t).toBeCloseTo(0, 5);
    expect(sampleGhostAtTime(99)!.t).toBeCloseTo(40, 5);
  });

  it('reports a negative delta when the player is ahead of the ghost', () => {
    setGhostLap(makeLap(40));
    // Ghost reached u=200 at t=20. Player is there at t=18 → ahead.
    const delta = ghostDeltaAt(200, 18);
    expect(delta).not.toBeNull();
    expect(delta!).toBeLessThan(-1);
  });

  it('reports a positive delta when the player is behind', () => {
    setGhostLap(makeLap(40));
    const delta = ghostDeltaAt(200, 24);
    expect(delta).not.toBeNull();
    expect(delta!).toBeGreaterThan(1);
  });

  it('round-trips through serialize/parse', () => {
    const lap = makeLap(32, 12);
    setGhostLap(lap);
    const parsed = parseGhostLap(serializeGhostLap(getGhostLap()));
    expect(parsed).not.toBeNull();
    expect(parsed!.duration).toBe(32);
    expect(parsed!.samples.length).toBe(12);
    expect(parsed!.samples[6]!.u).toBeCloseTo(lap.samples[6]!.u, 5);
  });

  it('rejects a payload that is not a real lap', () => {
    expect(parseGhostLap(null)).toBeNull();
    expect(parseGhostLap({ duration: 1, samples: [] })).toBeNull();
    setGhostLap({ duration: 2, samples: [] });
    expect(getGhostLap()).toBeNull();
  });
});

describe('ghost: sectors', () => {
  const L = 800;

  it('maps u onto 8 equal sectors', () => {
    expect(sectorIndex(0, L)).toBe(0);
    expect(sectorIndex(99, L)).toBe(0);
    expect(sectorIndex(100, L)).toBe(1);
    expect(sectorIndex(799, L)).toBe(7);
    expect(sectorBoundaryU(1, L)).toBeCloseTo(100, 5);
    expect(sectorBoundaryU(8, L)).toBeCloseTo(L, 5);
  });

  it('reports the sector just completed when u crosses a boundary', () => {
    expect(completedSector(90, 105, L)).toBe(1);
    expect(completedSector(190, 210, L)).toBe(2);
    expect(completedSector(50, 80, L)).toBeNull();
  });

  it('returns null on lap wrap so sector 8 is the lap time', () => {
    expect(completedSector(790, 5, L)).toBeNull();
    expect(completedSector(0, 0, 0)).toBeNull();
  });
});

describe('ghost: recording', () => {
  it('commits a recorded lap as the ghost when it is a personal best', () => {
    const state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('world-transform', WorldTransform);
    state.registerComponent('vehicle', Vehicle);
    state.registerComponent('player-vehicle', PlayerVehicle);
    state.registerComponent('race-tracker', RaceTracker);
    state.registerComponent('track', Track);

    const track = state.createEntity();
    state.addComponent(track, Track);
    const spline = oval();
    attachTrackSpline(track, spline);
    Track.length[track] = spline.length;

    const player = state.createEntity();
    state.addComponent(player, Transform);
    state.addComponent(player, WorldTransform);
    state.addComponent(player, Vehicle);
    state.addComponent(player, PlayerVehicle);
    state.addComponent(player, RaceTracker);
    Vehicle.trackS[player] = 0;
    Vehicle.trackLateral[player] = 0;
    Vehicle.heading[player] = 0;
    RaceTracker.lap[player] = 0;
    RaceTracker.distance[player] = 0;
    RaceTracker.lapStartTime[player] = 0;
    RaceTracker.lastLapTime[player] = -1;

    const mutableTime = state.time as { deltaTime: number };
    mutableTime.deltaTime = FIXED_DT;

    setRaceState({
      phase: 'racing',
      generation: 1,
      raceTime: 0,
      playerVehicle: player,
      track,
    });

    // First tick latches the generation and starts a fresh recording.
    GhostSystem.update?.(state);

    for (let i = 1; i <= 80; i++) {
      const t = i * 0.1;
      RaceTracker.distance[player] = i * 5;
      Vehicle.trackS[player] = i * 5;
      setRaceState({ ...getRaceState(), raceTime: t });
      GhostSystem.update?.(state);
    }

    expect(getGhostLap()).toBeNull();

    RaceTracker.lap[player] = 1;
    RaceTracker.lastLapTime[player] = 8;
    RaceTracker.distance[player] = 400;
    setRaceState({ ...getRaceState(), raceTime: 8 });
    GhostSystem.update?.(state);

    const ghost = getGhostLap();
    expect(ghost).not.toBeNull();
    expect(ghost!.duration).toBe(8);
    expect(ghost!.samples.length).toBeGreaterThan(8);
  });
});
