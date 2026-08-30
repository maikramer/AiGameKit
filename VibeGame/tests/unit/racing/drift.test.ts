import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  AiDriver,
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
  driftTier,
  DRIFT_TIER1_S,
  DRIFT_TIER2_S,
} from '../../../src/plugins/racing/vehicle-control';
import { startSpinOut } from '../../../src/plugins/racing/tricks';
import { drainRacingBanners } from '../../../src/plugins/racing/fx-events';
import {
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';

const FIXED_DT = 1 / 60;

/** Long gentle oval (same shape as vehicle.test.ts), wide enough to slide in. */
function ovalNodes(width = 34): TrackNode[] {
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

interface Harness {
  state: State;
  spline: TrackSpline;
  car: number;
  step(seconds: number): void;
}

function makeHarness(): Harness {
  const state = new State();
  state.registerComponent('transform', Transform);
  state.registerComponent('world-transform', WorldTransform);
  state.registerComponent('vehicle', Vehicle);
  state.registerComponent('player-vehicle', PlayerVehicle);
  state.registerComponent('ai-driver', AiDriver);
  state.registerComponent('race-tracker', RaceTracker);
  state.registerComponent('track', Track);

  const track = state.createEntity();
  state.addComponent(track, Track);
  Track.totalLaps[track] = 2;
  Track.shoulder[track] = 3;
  // Wall-free: these tests exercise charge and payout, not containment — a
  // full-lock committed drift arcs wide by design and must not become a
  // barrier grind that kills the speed (and the drift) mid-charge.
  Track.walls[track] = 0;

  const spline = new TrackSpline(ovalNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

  // Not a PlayerVehicle: the controller would overwrite inputs from the
  // keyboard — the test writes them directly.
  const car = state.createEntity();
  state.addComponent(car, Transform);
  state.addComponent(car, WorldTransform);
  state.addComponent(car, Vehicle);
  state.addComponent(car, RaceTracker);
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

  setRaceState({ phase: 'racing', playerVehicle: 0, track });

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
  // Banner shouts queue globally — drain so a payout fired in one test can't
  // leak into the next test's assertions.
  drainRacingBanners();
});

describe('drift: tier thresholds (pure)', () => {
  it('maps charge seconds onto payout tiers', () => {
    expect(driftTier(0)).toBe(0);
    expect(driftTier(DRIFT_TIER1_S - 0.01)).toBe(0);
    expect(driftTier(DRIFT_TIER1_S)).toBe(1);
    expect(driftTier(DRIFT_TIER2_S - 0.01)).toBe(1);
    expect(driftTier(DRIFT_TIER2_S)).toBe(2);
  });
});

describe('drift: charge builds while committed', () => {
  it('locks the drift direction to the steering and accumulates charge', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5);
    expect(Math.abs(Vehicle.speed[h.car])).toBeGreaterThan(14);

    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(0.6);
    expect(Vehicle.driftDir[h.car]).toBe(1);
    expect(Vehicle.driftCharge[h.car]).toBeGreaterThan(0.4);
  });

  it('does not commit below the drift speed', () => {
    const h = makeHarness();
    // Parked: handbrake + steer is just a handbrake, not a chargeable drift.
    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(1);
    expect(Vehicle.driftDir[h.car]).toBe(0);
    expect(Vehicle.driftCharge[h.car]).toBe(0);
  });
});

describe('drift: payout on release', () => {
  it('pays a tier-1 mini-turbo when a full charge is released', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5);
    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(DRIFT_TIER1_S + 0.3);
    expect(Vehicle.driftCharge[h.car]).toBeGreaterThanOrEqual(DRIFT_TIER1_S);

    Vehicle.handbrake[h.car] = 0;
    Vehicle.steerInput[h.car] = 0;
    h.step(FIXED_DT);
    expect(Vehicle.miniTurbo[h.car]).toBeGreaterThan(0.5);
    const banners = drainRacingBanners();
    expect(banners.some((b) => b.text === 'MINI-TURBO!')).toBe(true);
  });

  it('pays a tier-2 super turbo for a long charge', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5);
    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(DRIFT_TIER2_S + 0.25);
    Vehicle.handbrake[h.car] = 0;
    Vehicle.steerInput[h.car] = 0;
    h.step(FIXED_DT);
    expect(Vehicle.miniTurbo[h.car]).toBeGreaterThan(1.2);
    expect(drainRacingBanners().some((b) => b.text === 'SUPER TURBO!')).toBe(
      true
    );
  });

  it('pays nothing when released before the first tier', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5);
    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(0.4); // below DRIFT_TIER1_S
    Vehicle.handbrake[h.car] = 0;
    Vehicle.steerInput[h.car] = 0;
    h.step(FIXED_DT);
    expect(Vehicle.miniTurbo[h.car]).toBe(0);
    expect(drainRacingBanners().length).toBe(0);
  });

  it('a charged drift leaves the car faster than the same corner gripped', () => {
    // Same corner, same speed; one kart charges the drift and releases, the
    // other just slides to nothing. The payout has to be worth the show.
    const drifting = makeHarness();
    Vehicle.throttle[drifting.car] = 1;
    drifting.step(1.5);
    Vehicle.steerInput[drifting.car] = 1;
    Vehicle.handbrake[drifting.car] = 1;
    drifting.step(DRIFT_TIER1_S + 0.3);
    Vehicle.handbrake[drifting.car] = 0;
    Vehicle.steerInput[drifting.car] = 0;
    drifting.step(0.5);
    const driftSpeed = Vehicle.speed[drifting.car];

    const gripped = makeHarness();
    Vehicle.throttle[gripped.car] = 1;
    gripped.step(1.5);
    Vehicle.steerInput[gripped.car] = 1;
    Vehicle.handbrake[gripped.car] = 1;
    gripped.step(DRIFT_TIER1_S + 0.3);
    Vehicle.handbrake[gripped.car] = 0;
    // Forfeit the charge: spin out mid-drift, then drive on.
    startSpinOut(gripped.car);
    gripped.step(0.5);
    const gripSpeed = Vehicle.speed[gripped.car];

    expect(driftSpeed).toBeGreaterThan(gripSpeed + 1);
  });
});

describe('drift: losing the charge', () => {
  it('a spin-out forfeits the charge', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5);
    Vehicle.steerInput[h.car] = 1;
    Vehicle.handbrake[h.car] = 1;
    h.step(DRIFT_TIER1_S + 0.2);
    startSpinOut(h.car);
    h.step(FIXED_DT);
    expect(Vehicle.driftDir[h.car]).toBe(0);
    expect(Vehicle.driftCharge[h.car]).toBe(0);
    Vehicle.handbrake[h.car] = 0;
    Vehicle.steerInput[h.car] = 0;
    h.step(FIXED_DT);
    expect(Vehicle.miniTurbo[h.car]).toBe(0);
  });
});

describe('drift: steering feel', () => {
  it('unwinds the wheel faster than it winds it on', () => {
    // Keyboard input has no analogue return — the car must provide one. Ramp
    // 0 → full and hold, then release and count how much is left after the
    // same interval: the release has to be clearly further along.
    const h = makeHarness();
    Vehicle.steerInput[h.car] = 1;
    h.step(0.2);
    const ramped = Math.abs(Vehicle.steer[h.car]);
    Vehicle.steerInput[h.car] = 0;
    h.step(0.2);
    const released = Math.abs(Vehicle.steer[h.car]);
    expect(ramped).toBeGreaterThan(0.6);
    expect(released).toBeLessThan(0.2);
  });
});
