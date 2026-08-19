import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  AiDriver,
  HeldItem,
  ItemKind,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from '../../../src/plugins/racing/components';
import {
  attachTrackSpline,
  clearFireballs,
  clearTrackData,
  getFireballs,
} from '../../../src/plugins/racing/data';
import {
  TrackSpline,
  type TrackNode,
} from '../../../src/plugins/racing/spline';
import { placeVehicleOnTrack } from '../../../src/plugins/racing/vehicle-control';
import { vehicleSfxEdges } from '../../../src/plugins/racing/engine-audio';
import { ItemSystem, useHeldItem } from '../../../src/plugins/racing/items';
import { startSpinOut } from '../../../src/plugins/racing/tricks';
import {
  AiDriverSystem,
  resetAiMistakes,
  triggerAiMistake,
} from '../../../src/plugins/racing/ai-driver';
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

function tuneCar(eid: number): void {
  Vehicle.maxSpeed[eid] = 50;
  Vehicle.accel[eid] = 26;
  Vehicle.brake[eid] = 48;
  Vehicle.engineBrake[eid] = 7;
  Vehicle.reverseSpeed[eid] = 12;
  Vehicle.maxSteer[eid] = 2.6;
  Vehicle.steerSpeed[eid] = 10;
  Vehicle.grip[eid] = 7;
  Vehicle.driftGrip[eid] = 0.32;
  Vehicle.halfLength[eid] = 1.35;
  Vehicle.halfWidth[eid] = 0.85;
  Vehicle.rideHeight[eid] = 0.35;
  Vehicle.boostCapacity[eid] = 0;
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
  clearFireballs();
  resetAiMistakes();
});

describe('vehicle SFX edges', () => {
  it('fires crash on a fresh impact, not while the timer is already low', () => {
    const hit = vehicleSfxEdges({
      boosting: false,
      prevBoosting: false,
      impactTimer: 0.02,
      prevImpactTimer: 0.4,
      slip: 0,
      speed: 20,
      now: 1,
      skidReadyAt: 0,
    });
    expect(hit.crash).toBe(true);
    expect(hit.nitro).toBe(false);

    const held = vehicleSfxEdges({
      boosting: false,
      prevBoosting: false,
      impactTimer: 0.02,
      prevImpactTimer: 0.04,
      slip: 0,
      speed: 20,
      now: 1,
      skidReadyAt: 0,
    });
    expect(held.crash).toBe(false);
  });

  it('fires nitro on the rising edge of boost', () => {
    const start = vehicleSfxEdges({
      boosting: true,
      prevBoosting: false,
      impactTimer: 1,
      prevImpactTimer: 1,
      slip: 0,
      speed: 20,
      now: 1,
      skidReadyAt: 0,
    });
    expect(start.nitro).toBe(true);

    const held = vehicleSfxEdges({
      boosting: true,
      prevBoosting: true,
      impactTimer: 1,
      prevImpactTimer: 1,
      slip: 0,
      speed: 20,
      now: 1,
      skidReadyAt: 0,
    });
    expect(held.nitro).toBe(false);
  });

  it('fires skid when sliding fast, then waits for the cooldown', () => {
    const first = vehicleSfxEdges({
      boosting: false,
      prevBoosting: false,
      impactTimer: 1,
      prevImpactTimer: 1,
      slip: 0.8,
      speed: 16,
      now: 2,
      skidReadyAt: 0,
    });
    expect(first.skid).toBe(true);
    expect(first.nextSkidReadyAt).toBeCloseTo(2.7);

    const tooSoon = vehicleSfxEdges({
      boosting: false,
      prevBoosting: false,
      impactTimer: 1,
      prevImpactTimer: 1,
      slip: 0.8,
      speed: 16,
      now: 2.2,
      skidReadyAt: first.nextSkidReadyAt,
    });
    expect(tooSoon.skid).toBe(false);
  });
});

describe('fireball', () => {
  it('launches from the held slot and homes onto the kart ahead', () => {
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
    tuneCar(car);
    placeVehicleOnTrack(car, spline, 0, 0);

    const rival = state.createEntity();
    state.addComponent(rival, Transform);
    state.addComponent(rival, WorldTransform);
    state.addComponent(rival, Vehicle);
    state.addComponent(rival, RaceTracker);
    state.addComponent(rival, HeldItem);
    tuneCar(rival);
    placeVehicleOnTrack(rival, spline, 40, 1.5);
    Vehicle.speed[rival] = 30;

    HeldItem.item[car] = ItemKind.Fireball;
    expect(useHeldItem(car, spline)).toBe(true);
    expect(HeldItem.item[car]).toBe(ItemKind.None);
    expect(getFireballs().length).toBe(1);

    const mutableTime = state.time as { fixedDeltaTime: number };
    mutableTime.fixedDeltaTime = FIXED_DT;
    setRaceState({ phase: 'racing', track });
    // The fireball is faster than the kart it chases: it closes and hits.
    for (let i = 0; i < 240 && getFireballs().length > 0; i++) {
      ItemSystem.update?.(state);
    }
    expect(getFireballs().length).toBe(0);
    expect(Vehicle.spinOutTimer[rival]).toBeGreaterThan(0);
  });

  it('a latched shield eats the spin instead', () => {
    const state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('world-transform', WorldTransform);
    state.registerComponent('vehicle', Vehicle);
    state.registerComponent('race-tracker', RaceTracker);
    state.registerComponent('track', Track);
    state.registerComponent('held-item', HeldItem);

    const track = state.createEntity();
    state.addComponent(track, Track);
    const spline = new TrackSpline(ovalNodes(), { step: 2 });
    attachTrackSpline(track, spline);

    const car = state.createEntity();
    state.addComponent(car, Transform);
    state.addComponent(car, Vehicle);
    state.addComponent(car, RaceTracker);
    state.addComponent(car, HeldItem);

    HeldItem.shieldArmed[car] = 1;
    HeldItem.shieldTime[car] = 5;
    expect(startSpinOut(car)).toBe('blocked');
    expect(Vehicle.spinOutTimer[car]).toBe(0);
    expect(HeldItem.shieldArmed[car]).toBe(0);
  });
});

describe('AI mistakes', () => {
  it('lock-up zeros throttle after one driver tick', () => {
    const state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('world-transform', WorldTransform);
    state.registerComponent('vehicle', Vehicle);
    state.registerComponent('ai-driver', AiDriver);
    state.registerComponent('race-tracker', RaceTracker);
    state.registerComponent('track', Track);

    const track = state.createEntity();
    state.addComponent(track, Track);
    Track.shoulder[track] = 3;
    Track.walls[track] = 1;
    const spline = new TrackSpline(ovalNodes(), { step: 2 });
    attachTrackSpline(track, spline);
    Track.length[track] = spline.length;

    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, WorldTransform);
    state.addComponent(eid, Vehicle);
    state.addComponent(eid, RaceTracker);
    state.addComponent(eid, AiDriver);
    tuneCar(eid);
    AiDriver.skill[eid] = 0.7;
    AiDriver.rubberBand[eid] = 0;
    AiDriver.lineOffset[eid] = 0;
    AiDriver.steerState[eid] = 0;
    AiDriver.noisePhase[eid] = 0;
    AiDriver.stuckTimer[eid] = 0;
    placeVehicleOnTrack(eid, spline, 40, 0);
    Vehicle.speed[eid] = 22;
    Vehicle.throttle[eid] = 1;
    Vehicle.brakeInput[eid] = 0;

    setRaceState({ phase: 'racing', track });
    const mutableTime = state.time as {
      fixedDeltaTime: number;
      elapsed: number;
    };
    mutableTime.fixedDeltaTime = FIXED_DT;
    mutableTime.elapsed = 0;

    triggerAiMistake(eid, 'lockup', 1, 0);
    AiDriverSystem.update?.(state);

    expect(Vehicle.throttle[eid]).toBe(0);
    expect(Vehicle.brakeInput[eid]).toBeCloseTo(0.9);
  });
});
