import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  AiDriver,
  PlayerVehicle,
  PowerUp,
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
import { CheckpointSystem } from '../../../src/plugins/racing/checkpoints';
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
  track: number;
  car: number;
  step(seconds: number): void;
} {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('world-transform', WorldTransform);
  state.registerComponent('vehicle', Vehicle);
  state.registerComponent('player-vehicle', PlayerVehicle);
  state.registerComponent('ai-driver', AiDriver);
  state.registerComponent('race-tracker', RaceTracker);
  state.registerComponent('track', Track);
  state.registerComponent('power-up', PowerUp);

  const track = state.createEntity();
  state.addComponent(track, Track);
  Track.totalLaps[track] = 2;
  Track.shoulder[track] = 3;
  Track.walls[track] = 1;
  Track.checkpointCount[track] = 8;

  const spline = new TrackSpline(ovalNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

  const car = state.createEntity();
  state.addComponent(car, Transform);
  state.addComponent(car, WorldTransform);
  state.addComponent(car, Vehicle);
  state.addComponent(car, PlayerVehicle);
  state.addComponent(car, RaceTracker);
  state.addComponent(car, PowerUp);
  Vehicle.maxSpeed[car] = 50;
  Vehicle.accel[car] = 26;
  Vehicle.brake[car] = 48;
  Vehicle.engineBrake[car] = 7;
  Vehicle.reverseSpeed[car] = 12;
  Vehicle.maxSteer[car] = 2.6;
  Vehicle.steerSpeed[car] = 10;
  Vehicle.grip[car] = 7;
  Vehicle.driftGrip[car] = 0.32;
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
    track,
    car,
    step(seconds: number) {
      const steps = Math.round(seconds / FIXED_DT);
      for (let i = 0; i < steps; i++) {
        VehicleControlSystem.update?.(state);
        CheckpointSystem.update?.(state);
      }
    },
  };
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
});

describe('checkpoints', () => {
  it('records a checkpoint when the car passes it', () => {
    const h = makeHarness();
    // Drive a short distance so the car crosses the first checkpoint
    // (length/8 metres in). The oval is ~9.4 km, so the first checkpoint is
    // far ahead; instead manually advance the tracker to simulate a pass.
    const firstS = h.spline.length / 8;
    RaceTracker.lastCheckpointS[h.car] = 0;
    RaceTracker.lastCheckpointIndex[h.car] = 0;
    Vehicle.trackS[h.car] = firstS + 2;
    CheckpointSystem.update?.(h.state);
    expect(RaceTracker.lastCheckpointIndex[h.car]).toBe(1);
    expect(RaceTracker.lastCheckpointS[h.car]).toBeCloseTo(firstS, 1);
  });

  it('respawns the car at the last checkpoint when stuck off-track', () => {
    const h = makeHarness();
    const firstS = h.spline.length / 8;
    RaceTracker.lastCheckpointS[h.car] = firstS;
    RaceTracker.lastCheckpointIndex[h.car] = 1;
    // Park the car far off the road and give it a big off-track timer.
    Vehicle.trackS[h.car] = firstS + 4;
    Vehicle.trackLateral[h.car] = 14;
    Vehicle.speed[h.car] = 10;
    RaceTracker.offTrackTimer[h.car] = 2;
    // No shield armed → respawn happens.
    PowerUp.shieldArmed[h.car] = 0;
    CheckpointSystem.update?.(h.state);
    // The car should be back near the checkpoint, stopped, with a penalty.
    expect(Math.abs(Vehicle.trackS[h.car] - firstS)).toBeLessThan(5);
    expect(Vehicle.speed[h.car]).toBeLessThan(1);
    expect(RaceTracker.respawnFlash[h.car]).toBe(1);
  });

  it('a shield absorbs the respawn', () => {
    const h = makeHarness();
    const firstS = h.spline.length / 8;
    RaceTracker.lastCheckpointS[h.car] = firstS;
    RaceTracker.lastCheckpointIndex[h.car] = 1;
    Vehicle.trackS[h.car] = firstS + 4;
    Vehicle.trackLateral[h.car] = 14;
    Vehicle.speed[h.car] = 10;
    RaceTracker.offTrackTimer[h.car] = 2;
    // Shield armed and its latch still running.
    PowerUp.shieldArmed[h.car] = 1;
    PowerUp.cd2[h.car] = 3;
    CheckpointSystem.update?.(h.state);
    // The shield consumed the respawn: the car stays put, shield is spent.
    expect(Vehicle.speed[h.car]).toBeGreaterThan(5);
    expect(PowerUp.shieldArmed[h.car]).toBe(0);
    expect(RaceTracker.respawnFlash[h.car]).toBe(0);
  });
});
