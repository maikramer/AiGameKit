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
  attachTrackSpline,
  clearTrackData,
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
  TrickSystem,
  startSpinOut,
  startTrick,
  TrickKind,
} from '../../../src/plugins/racing/tricks';
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
  Vehicle.boostCapacity[car] = 2;
  Vehicle.boost[car] = 0;
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
      for (let i = 0; i < steps; i++) {
        VehicleControlSystem.update?.(state);
        TrickSystem.update?.(state);
      }
    },
  };
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
});

describe('stunts', () => {
  it('only starts mid-air and only one at a time', () => {
    const h = makeHarness();
    expect(startTrick(h.car, TrickKind.Spin360)).toBe(false);
    Vehicle.airborne[h.car] = 1;
    expect(startTrick(h.car, TrickKind.Spin360)).toBe(true);
    expect(startTrick(h.car, TrickKind.RollLeft)).toBe(false);
  });

  it('pays nitro when the rotation completes before landing', () => {
    const h = makeHarness();
    // Hang the car high enough that the fall (gravity 22) outlasts the 0.75 s
    // rotation, so the stunt completes in the air before touchdown.
    Vehicle.airborne[h.car] = 1;
    Vehicle.airHeight[h.car] = 12;
    Vehicle.verticalSpeed[h.car] = 0;
    startTrick(h.car, TrickKind.Spin360);
    h.step(0.8);
    expect(Vehicle.trickSpin[h.car]).toBeGreaterThanOrEqual(Math.PI * 2 - 0.03);
    expect(Vehicle.trickActive[h.car]).toBe(1);
    expect(Vehicle.airborne[h.car]).toBe(1);

    // Touch down: reward lands, stunt state clears. Zero the tank first so
    // the reward is unambiguous.
    Vehicle.boost[h.car] = 0;
    Vehicle.airHeight[h.car] = 0.36;
    Vehicle.verticalSpeed[h.car] = -1;
    h.step(0.2);
    expect(Vehicle.trickActive[h.car]).toBe(0);
    expect(Vehicle.boost[h.car]).toBeGreaterThan(0.5);
  });

  it('punishes a rotation abandoned half-way', () => {
    const h = makeHarness();
    Vehicle.airborne[h.car] = 1;
    Vehicle.airHeight[h.car] = 6;
    Vehicle.speed[h.car] = 30;
    startTrick(h.car, TrickKind.RollLeft);
    h.step(0.2); // less than the 0.75 s the rotation needs
    Vehicle.airHeight[h.car] = 0.36;
    Vehicle.verticalSpeed[h.car] = -1;
    h.step(0.2);
    expect(Vehicle.trickActive[h.car]).toBe(0);
    // Bailed out: well under the entry speed after the face-plant.
    expect(Vehicle.speed[h.car]).toBeLessThan(26);
  });
});

describe('spin-out', () => {
  it('ignores driver input and washes off speed while active', () => {
    const h = makeHarness();
    placeVehicleOnTrack(h.car, h.spline, 400, 0);
    Vehicle.speed[h.car] = 30;
    Vehicle.steerInput[h.car] = 1;
    Vehicle.throttle[h.car] = 1;
    expect(startSpinOut(h.car)).toBe('spun');
    const speed0 = Vehicle.speed[h.car];
    h.step(0.5);
    expect(Vehicle.steerInput[h.car]).toBe(0);
    expect(Vehicle.throttle[h.car]).toBe(0);
    expect(Vehicle.speed[h.car]).toBeLessThan(speed0);
    // It recovers on its own.
    h.step(1.5);
    expect(Vehicle.spinOutTimer[h.car]).toBe(0);
  });
});
