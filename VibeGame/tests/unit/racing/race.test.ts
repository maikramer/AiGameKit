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
import { VehicleControlSystem } from '../../../src/plugins/racing/vehicle-control';
import { AiDriverSystem } from '../../../src/plugins/racing/ai-driver';
import {
  RaceDirectorSystem,
  getStandings,
  getVehicleName,
  setVehicleName,
} from '../../../src/plugins/racing/race-director';
import {
  getRaceState,
  holdRaceOnGrid,
  markRaceReady,
  resetRaceState,
  restartRace,
} from '../../../src/plugins/racing/race-state';

const FIXED_DT = 1 / 60;

/** A compact circuit an AI can lap in well under a minute. */
function circuitNodes(): TrackNode[] {
  return [
    { x: 0, y: 0, z: -140, width: 16 },
    { x: 120, y: 0, z: -140, width: 16 },
    { x: 240, y: 0, z: -140, width: 16 },
    { x: 320, y: 0, z: -60, width: 15 },
    { x: 320, y: 2, z: 60, width: 15 },
    { x: 240, y: 4, z: 140, width: 15 },
    { x: 60, y: 2, z: 150, width: 16 },
    { x: -80, y: 0, z: 110, width: 16 },
    { x: -120, y: 0, z: 0, width: 16 },
    { x: -80, y: 0, z: -110, width: 16 },
    // Rejoin the straight on an arc: the earlier draft jogged to (-120, -140)
    // and back, a near-90° kink that told us more about the corner than about
    // the driver being tested.
    { x: -40, y: 0, z: -150, width: 16 },
  ];
}

interface RaceHarness {
  state: State;
  spline: TrackSpline;
  track: number;
  cars: number[];
  player: number;
  /**
   * A car with neither `PlayerVehicle` nor `AiDriver`, so a test can write its
   * pedals directly: the controller reads the *player* car from the keyboard
   * and the AI writes over everything else.
   */
  manual: number;
  frame(seconds?: number): void;
}

function makeRace(
  options: { laps?: number; rivals?: number; autopilot?: boolean } = {}
): RaceHarness {
  const { laps = 2, rivals = 2, autopilot = true } = options;
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
  Track.totalLaps[track] = laps;
  Track.shoulder[track] = 3;
  Track.walls[track] = 1;
  const spline = new TrackSpline(circuitNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

  const cars: number[] = [];
  const makeCar = (isPlayer: boolean, name: string, skill: number): number => {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, WorldTransform);
    state.addComponent(eid, Vehicle);
    state.addComponent(eid, RaceTracker);
    if (isPlayer) state.addComponent(eid, PlayerVehicle);
    // The player is given an AI too: that is the plugin's attract mode, and it
    // is what makes a whole race runnable without a keyboard.
    if (!isPlayer || autopilot) {
      state.addComponent(eid, AiDriver);
      AiDriver.skill[eid] = skill;
      AiDriver.rubberBand[eid] = 0;
      AiDriver.lineOffset[eid] = 0;
      AiDriver.steerState[eid] = 0;
      AiDriver.noisePhase[eid] = 0;
      AiDriver.stuckTimer[eid] = 0;
    }
    Vehicle.maxSpeed[eid] = 44;
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
    setVehicleName(eid, name);
    cars.push(eid);
    return eid;
  };

  const player = makeCar(true, 'Player', 0.9);
  for (let i = 0; i < rivals; i++) makeCar(false, `Rival ${i + 1}`, 0.85);
  const manual = makeManualCar();

  function makeManualCar(): number {
    const eid = state.createEntity();
    state.addComponent(eid, Transform);
    state.addComponent(eid, WorldTransform);
    state.addComponent(eid, Vehicle);
    state.addComponent(eid, RaceTracker);
    Vehicle.maxSpeed[eid] = 44;
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
    setVehicleName(eid, 'Manual');
    cars.push(eid);
    return eid;
  }

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
    cars,
    player,
    manual,
    frame(seconds = FIXED_DT) {
      const steps = Math.max(1, Math.round(seconds / FIXED_DT));
      for (let i = 0; i < steps; i++) {
        AiDriverSystem.update?.(state);
        VehicleControlSystem.update?.(state);
        RaceDirectorSystem.update?.(state);
      }
    },
  };
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
});

describe('race director: phases', () => {
  it('forms a grid, counts down, then goes racing', () => {
    const h = makeRace();
    expect(getRaceState().phase).toBe('idle');

    h.frame();
    expect(getRaceState().phase).toBe('grid');
    // Two rivals, the player, and the manual car the tests drive by hand.
    expect(getRaceState().entrants).toBe(4);

    // Cars are parked past the start line, alternating sides, stopped.
    for (const car of h.cars) {
      expect(Vehicle.speed[car]).toBe(0);
      expect(Vehicle.trackS[car]).toBeGreaterThan(0);
      expect(Math.abs(Vehicle.trackLateral[car])).toBeGreaterThan(1);
    }
    // Pole belongs to the player.
    expect(RaceTracker.gridSlot[h.player]).toBe(0);

    h.frame(1.2);
    expect(getRaceState().phase).toBe('countdown');
    h.frame(3.2);
    expect(getRaceState().phase).toBe('racing');
    expect(getRaceState().raceTime).toBeLessThan(0.5);
  });

  it('holds the grid until the game says its assets are ready', () => {
    holdRaceOnGrid();
    const h = makeRace();
    h.frame(4);
    expect(getRaceState().phase).toBe('grid');
    markRaceReady();
    h.frame(1.5);
    expect(getRaceState().phase).toBe('countdown');
  });

  it('keeps the cars stationary until the lights go out', () => {
    const h = makeRace();
    h.frame(2);
    for (const car of h.cars) expect(Vehicle.speed[car]).toBe(0);
    h.frame(3.5);
    h.frame(1);
    expect(Vehicle.speed[h.player]).toBeGreaterThan(1);
  });
});

describe('race director: scoring', () => {
  it('counts laps, times them and finishes the race', () => {
    const h = makeRace({ laps: 2 });
    h.frame(5); // grid + countdown
    expect(getRaceState().phase).toBe('racing');

    // Race until the player takes the flag (or we give up).
    let elapsed = 0;
    while (getRaceState().phase !== 'finished' && elapsed < 240) {
      h.frame(0.5);
      elapsed += 0.5;
    }

    expect(getRaceState().phase).toBe('finished');
    expect(RaceTracker.lap[h.player]).toBe(2);
    expect(RaceTracker.bestLapTime[h.player]).toBeGreaterThan(5);
    expect(RaceTracker.lastLapTime[h.player]).toBeGreaterThan(5);
    expect(RaceTracker.finishTime[h.player]).toBeGreaterThan(
      RaceTracker.bestLapTime[h.player]
    );

    const results = getRaceState().results;
    expect(results).toHaveLength(4);
    expect(results[0]!.position).toBe(1);
    expect(results.some((r) => r.isPlayer)).toBe(true);
    expect(results.map((r) => r.position)).toEqual([1, 2, 3, 4]);
  });

  it('ranks cars by distance covered', () => {
    const h = makeRace({ laps: 3 });
    h.frame(5);
    h.frame(25);

    const standings = getStandings();
    expect(standings).toHaveLength(4);
    for (let i = 1; i < standings.length; i++) {
      expect(RaceTracker.distance[standings[i - 1]!]).toBeGreaterThanOrEqual(
        RaceTracker.distance[standings[i]!]
      );
    }
    expect(RaceTracker.position[standings[0]!]).toBe(1);
  });

  it('does not credit a lap for reversing over the line', () => {
    const h = makeRace({ laps: 3, rivals: 0 });
    h.frame(5);
    // Drive backwards from the grid, back across the start line.
    for (let i = 0; i < 60 * 8; i++) {
      Vehicle.brakeInput[h.manual] = 1;
      h.frame();
    }
    expect(Vehicle.speed[h.manual]).toBeLessThan(0);
    expect(RaceTracker.lap[h.manual]).toBe(0);
    expect(RaceTracker.distance[h.manual]).toBeLessThan(0);
  });

  it('flags a car that is driving the wrong way', () => {
    const h = makeRace({ laps: 3, rivals: 0 });
    h.frame(5);
    for (let i = 0; i < 60 * 6; i++) {
      Vehicle.brakeInput[h.manual] = 1;
      h.frame();
    }
    expect(RaceTracker.wrongWay[h.manual]).toBe(1);

    for (let i = 0; i < 60 * 5; i++) {
      Vehicle.brakeInput[h.manual] = 0;
      Vehicle.throttle[h.manual] = 1;
      h.frame();
    }
    expect(RaceTracker.wrongWay[h.manual]).toBe(0);
  });

  it('restarts back onto the grid with the clock and laps reset', () => {
    const h = makeRace({ laps: 3 });
    h.frame(5);
    h.frame(30);
    expect(RaceTracker.distance[h.player]).toBeGreaterThan(50);

    restartRace();
    h.frame();
    expect(getRaceState().phase).toBe('grid');
    for (const car of h.cars) {
      expect(RaceTracker.lap[car]).toBe(0);
      expect(RaceTracker.distance[car]).toBe(0);
      expect(Vehicle.speed[car]).toBe(0);
    }
    h.frame(5);
    expect(getRaceState().phase).toBe('racing');
  });

  it('remembers driver names for the results table', () => {
    const h = makeRace();
    expect(getVehicleName(h.player)).toBe('Player');
    expect(getVehicleName(h.cars[1]!)).toBe('Rival 1');
  });
});

describe('rival AI', () => {
  it('drives a full lap without help', () => {
    const h = makeRace({ laps: 3, rivals: 1 });
    h.frame(5);
    const rival = h.cars[1]!;
    let elapsed = 0;
    while (RaceTracker.lap[rival] < 1 && elapsed < 180) {
      h.frame(0.5);
      elapsed += 0.5;
    }
    expect(RaceTracker.lap[rival]).toBeGreaterThanOrEqual(1);
    expect(RaceTracker.bestLapTime[rival]).toBeGreaterThan(5);
    expect(RaceTracker.bestLapTime[rival]).toBeLessThan(120);
  });

  it('stays on the racing surface', () => {
    const h = makeRace({ laps: 3, rivals: 1 });
    h.frame(5);
    const rival = h.cars[1]!;
    let worst = 0;
    for (let i = 0; i < 120; i++) {
      h.frame(0.25);
      const halfRoad = h.spline.sampleAt(Vehicle.trackS[rival]).width * 0.5;
      worst = Math.max(worst, Math.abs(Vehicle.trackLateral[rival]) - halfRoad);
    }
    // Allowed to put a wheel on the kerb, not to spend the lap in the gravel.
    expect(worst).toBeLessThan(2.5);
  });

  it('slows down for the corners', () => {
    const h = makeRace({ laps: 3, rivals: 1 });
    h.frame(5);
    const rival = h.cars[1]!;
    let straightSpeed = 0;
    let cornerSpeed = Infinity;
    for (let i = 0; i < 400; i++) {
      h.frame(0.1);
      const curve = Math.abs(h.spline.curvatureAt(Vehicle.trackS[rival]));
      const speed = Vehicle.speed[rival];
      if (curve < 0.002) straightSpeed = Math.max(straightSpeed, speed);
      if (curve > 0.012) cornerSpeed = Math.min(cornerSpeed, speed);
    }
    expect(straightSpeed).toBeGreaterThan(20);
    expect(cornerSpeed).toBeLessThan(straightSpeed);
  });

  it('is faster when it is more skilled', () => {
    const fast = makeRace({ laps: 3, rivals: 1 });
    AiDriver.skill[fast.cars[1]!] = 1;
    fast.frame(5);
    fast.frame(40);
    const fastDistance = RaceTracker.distance[fast.cars[1]!];
    resetRaceState();
    clearTrackData();

    const slow = makeRace({ laps: 3, rivals: 1 });
    AiDriver.skill[slow.cars[1]!] = 0.4;
    slow.frame(5);
    slow.frame(40);
    const slowDistance = RaceTracker.distance[slow.cars[1]!];

    expect(fastDistance).toBeGreaterThan(slowDistance);
  });
});
