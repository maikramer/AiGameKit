import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  HeldItem,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from '../../../src/plugins/racing/components';
import {
  addTrackRamp,
  attachTrackSpline,
  clearTrackData,
  rampHeightAt,
} from '../../../src/plugins/racing/data';
import {
  TrackSpline,
  type TrackNode,
} from '../../../src/plugins/racing/spline';
import {
  VehicleControlSystem,
  placeVehicleOnTrack,
} from '../../../src/plugins/racing/vehicle-control';
import {
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';

const FIXED_DT = 1 / 60;

function ovalNodes(width = 16): TrackNode[] {
  return [
    { x: 0, y: 0, z: -600, width },
    { x: 600, y: 0, z: -600, width },
    { x: 1200, y: 0, z: -600, width },
    { x: 1500, y: 0, z: 0, width },
    { x: 1200, y: 0, z: 600, width },
    { x: 0, y: 0, z: 600, width },
    { x: -1200, y: 0, z: 600, width },
    { x: -1500, y: 0, z: 0, width },
    { x: -1200, y: 0, z: -600, width },
    { x: -600, y: 0, z: -600, width },
  ];
}

function makeHarness(): {
  state: State;
  spline: TrackSpline;
  car: number;
  step(seconds: number): void;
} {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('world-transform', WorldTransform);
  state.registerComponent('vehicle', Vehicle);
  state.registerComponent('player-vehicle', PlayerVehicle);
  state.registerComponent('race-tracker', RaceTracker);
  state.registerComponent('track', Track);
  state.registerComponent('held-item', HeldItem);

  const track = state.createEntity();
  state.addComponent(track, Track);
  Track.shoulder[track] = 3;
  Track.walls[track] = 1;
  const spline = new TrackSpline(ovalNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

  const car = state.createEntity();
  state.addComponent(car, Transform);
  state.addComponent(car, WorldTransform);
  state.addComponent(car, Vehicle);
  state.addComponent(car, PlayerVehicle);
  state.addComponent(car, RaceTracker);
  state.addComponent(car, HeldItem);
  Vehicle.maxSpeed[car] = 50;
  Vehicle.accel[car] = 26;
  Vehicle.brake[car] = 48;
  Vehicle.engineBrake[car] = 7;
  Vehicle.maxSteer[car] = 2.6;
  Vehicle.steerSpeed[car] = 10;
  Vehicle.grip[car] = 7;
  Vehicle.halfLength[car] = 1.35;
  Vehicle.halfWidth[car] = 0.85;
  Vehicle.rideHeight[car] = 0.35;
  Vehicle.boostCapacity[car] = 0;
  placeVehicleOnTrack(car, spline, 0, 0);

  setRaceState({ phase: 'racing', playerVehicle: car, track });
  const mutableTime = state.time as {
    fixedDeltaTime: number;
    deltaTime: number;
  };
  mutableTime.fixedDeltaTime = FIXED_DT;
  mutableTime.deltaTime = FIXED_DT;

  return {
    state,
    spline,
    car,
    step(seconds: number) {
      const steps = Math.round(seconds / FIXED_DT);
      for (let i = 0; i < steps; i++) VehicleControlSystem.update?.(state);
    },
  };
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
});

describe('ramp profile', () => {
  it('is linear across the span and zero outside it', () => {
    const h = makeHarness();
    addTrackRamp(200, 10, 8, 2.5);
    expect(rampHeightAt(200, 0)).toBeCloseTo(0);
    expect(rampHeightAt(205, 0)).toBeCloseTo(1.25);
    expect(rampHeightAt(210, 0)).toBeCloseTo(2.5);
    expect(rampHeightAt(210.01, 0)).toBe(0);
    // Lateral gating: off the ramp's width there is nothing to climb.
    expect(rampHeightAt(205, 5)).toBe(0);
    void h;
  });
});

describe('ramp launch', () => {
  it('climbs the wedge and launches off the lip at speed', () => {
    const h = makeHarness();
    addTrackRamp(200, 10, 8, 2.5);
    // Drop the car just before the ramp at a healthy clip.
    placeVehicleOnTrack(h.car, h.spline, 197, 0);
    Vehicle.trackS[h.car] = 197;
    Vehicle.speed[h.car] = 30;
    Vehicle.heading[h.car] = Math.atan2(
      h.spline.sampleAt(197).tx,
      h.spline.sampleAt(197).tz
    );

    let maxClimb = 0;
    let launched = false;
    let launchVertical = 0;
    for (let i = 0; i < 90; i++) {
      VehicleControlSystem.update?.(h.state);
      if (Vehicle.airborne[h.car] === 1 && !launched) {
        launched = true;
        launchVertical = Vehicle.verticalSpeed[h.car] ?? 0;
      } else if (Vehicle.airborne[h.car] !== 1) {
        maxClimb = Math.max(maxClimb, Vehicle.airHeight[h.car] ?? 0);
      }
    }

    // Climbed the wedge…
    expect(maxClimb).toBeGreaterThan(1.5);
    // …and left the lip in the air with a slope-scaled vertical speed.
    // slope = height / length = 0.25; v = 0.25 × ~25 m/s ≈ 6 m/s.
    expect(launched).toBe(true);
    expect(launchVertical).toBeGreaterThan(4);
    expect(launchVertical).toBeLessThan(11);
  });
});
