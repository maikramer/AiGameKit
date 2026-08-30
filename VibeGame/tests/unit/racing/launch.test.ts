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
  evaluateLaunch,
  drawLaunchDelay,
  LAUNCH_OVERREV_S,
} from '../../../src/plugins/racing/vehicle-control';
import { drainRacingBanners } from '../../../src/plugins/racing/fx-events';
import {
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';

const FIXED_DT = 1 / 60;

function ovalNodes(width = 20): TrackNode[] {
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
  Track.walls[track] = 1;

  const spline = new TrackSpline(ovalNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

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

  setRaceState({ phase: 'countdown', countdown: 3, playerVehicle: 0, track });

  const mutableTime = state.time as {
    fixedDeltaTime: number;
    deltaTime: number;
  };
  mutableTime.fixedDeltaTime = FIXED_DT;
  mutableTime.deltaTime = FIXED_DT;

  return {
    state,
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

describe('launch: quality evaluation (pure)', () => {
  it('maps revs + redline time onto the four outcomes', () => {
    expect(evaluateLaunch(0.2, 0)).toBe('none');
    expect(evaluateLaunch(0.5, 0)).toBe('decent');
    expect(evaluateLaunch(0.72, 0)).toBe('rocket');
    expect(evaluateLaunch(1, LAUNCH_OVERREV_S)).toBe('rocket');
    expect(evaluateLaunch(1, LAUNCH_OVERREV_S + 0.01)).toBe('wheelspin');
    // Bogged down even at low revs if the tyres were lit up too long.
    expect(evaluateLaunch(0.4, 2)).toBe('wheelspin');
  });
});

describe('launch: rival plan (pure)', () => {
  it('skilled drivers always land inside the hold window', () => {
    for (let i = 0; i < 50; i++) {
      const delay = drawLaunchDelay(1, Math.random);
      expect(delay).toBeGreaterThanOrEqual(0.35);
      expect(delay).toBeLessThanOrEqual(2.2);
    }
  });

  it('weak drivers sometimes botch it in both directions', () => {
    let tooEarly = false;
    let tooLate = false;
    for (let i = 0; i < 500; i++) {
      const delay = drawLaunchDelay(0.1, Math.random);
      if (delay < 0.3) tooEarly = true;
      if (delay > 2.2) tooLate = true;
    }
    expect(tooEarly).toBe(true);
    expect(tooLate).toBe(true);
  });
});

describe('launch: revs on the grid', () => {
  it('throttle builds revs while the car stays planted', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(2);
    expect(Vehicle.launchRev[h.car]).toBeGreaterThanOrEqual(0.72);
    expect(Vehicle.speed[h.car]).toBe(0);
    // The engine sings: revs drive the audible rpm on the grid.
    expect(Vehicle.rpm[h.car]).toBeGreaterThan(0.7);
  });

  it('revs bleed back down when the throttle is released', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.2);
    const revved = Vehicle.launchRev[h.car];
    Vehicle.throttle[h.car] = 0;
    h.step(0.6);
    expect(Vehicle.launchRev[h.car]).toBeLessThan(revved * 0.55);
  });
});

describe('launch: the green light', () => {
  it('revs in the window turn into a rocket start', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(1.5); // revs pinned, redline time still inside the window
    setRaceState({ phase: 'racing', countdown: 0 });
    h.step(FIXED_DT);
    expect(Vehicle.speed[h.car]).toBeGreaterThan(5);
    expect(Vehicle.miniTurbo[h.car]).toBeGreaterThan(0.5);
    expect(drainRacingBanners().some((b) => b.text === 'ROCKET START!')).toBe(
      true
    );
  });

  it('pinning the throttle through the whole countdown wheelspins', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(3.0); // pinned from the first light: redline time > 1.6 s
    setRaceState({ phase: 'racing', countdown: 0 });
    h.step(FIXED_DT);
    expect(Vehicle.wheelspin[h.car]).toBeGreaterThan(0);
    expect(drainRacingBanners().some((b) => b.text === 'WHEELSPIN!')).toBe(
      true
    );
  });

  it('a rocket start beats a wheelspin off the line', () => {
    const rocket = makeHarness();
    Vehicle.throttle[rocket.car] = 1;
    rocket.step(1.5);
    setRaceState({ phase: 'racing', countdown: 0 });
    rocket.step(1.2);
    const rocketSpeed = Vehicle.speed[rocket.car];

    const spun = makeHarness();
    Vehicle.throttle[spun.car] = 1;
    spun.step(3.0);
    setRaceState({ phase: 'racing', countdown: 0 });
    spun.step(1.2);
    const spunSpeed = Vehicle.speed[spun.car];

    expect(rocketSpeed).toBeGreaterThan(spunSpeed + 2);
  });

  it('no revs, no launch: a sleeping start is just a start', () => {
    const h = makeHarness();
    h.step(2.5); // never touches the throttle
    setRaceState({ phase: 'racing', countdown: 0 });
    h.step(FIXED_DT);
    expect(Vehicle.miniTurbo[h.car]).toBe(0);
    expect(Vehicle.wheelspin[h.car]).toBe(0);
    expect(drainRacingBanners().length).toBe(0);
  });
});
