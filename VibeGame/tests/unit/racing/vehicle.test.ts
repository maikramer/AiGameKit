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
  addTrackObstacle,
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
  getRaceState,
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';
import {
  clearAllInput,
  handleKeyDown,
  handleKeyUp,
  setFocusedCanvas,
} from '../../../src/plugins/input/utils';

const FIXED_DT = 1 / 60;

/**
 * A very long, very gentle circuit: a car driven in a straight line stays on
 * the road for the whole of a test rather than grinding along a barrier, which
 * would quietly cap its speed and hide whatever the test was actually about.
 */
function ovalNodes(width = 16): TrackNode[] {
  // Node 0 sits in the middle of a run of collinear points, so the tangent at
  // s = 0 is exactly along the straight. Start a car on a node whose Catmull-Rom
  // neighbours pull the curve sideways and it drives off the road on its own —
  // which looks like a physics bug and is really a track-authoring one.
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

/** A tight circuit, for the tests that *want* the car to meet a barrier. */
function tightNodes(width = 16): TrackNode[] {
  return [
    { x: -120, y: 0, z: -60, width },
    { x: 120, y: 0, z: -60, width },
    { x: 170, y: 0, z: 0, width },
    { x: 120, y: 0, z: 60, width },
    { x: -120, y: 0, z: 60, width },
    { x: -170, y: 0, z: 0, width },
  ];
}

interface Harness {
  state: State;
  spline: TrackSpline;
  track: number;
  car: number;
  step(seconds: number): void;
}

function makeHarness(
  options: {
    nodes?: TrackNode[];
    shoulder?: number;
    walls?: boolean;
    spline?: TrackSpline;
    player?: boolean;
  } = {}
): Harness {
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
  Track.shoulder[track] = options.shoulder ?? 3;
  Track.walls[track] = options.walls === false ? 0 : 1;

  const spline =
    options.spline ??
    new TrackSpline(options.nodes ?? ovalNodes(), { step: 2 });
  attachTrackSpline(track, spline);
  Track.length[track] = spline.length;

  // Deliberately *not* a PlayerVehicle: the controller reads a player car's
  // inputs from the keyboard, which would overwrite whatever a test wrote.
  // (The keyboard-bindings tests opt in via `player: true` — they inject keys
  // through the input plugin's own handlers.)
  const car = state.createEntity();
  state.addComponent(car, Transform);
  state.addComponent(car, WorldTransform);
  state.addComponent(car, Vehicle);
  state.addComponent(car, RaceTracker);
  if (options.player) state.addComponent(car, PlayerVehicle);
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
    track,
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

describe('vehicle: longitudinal', () => {
  it('accelerates on throttle and tapers toward the top speed', () => {
    // A wide oval so the car can settle close to its rated top speed. The test
    // checks the car *reaches* a high speed quickly and stays near it, not the
    // instantaneous speed at the exact final frame (a later corner can scrub it).
    const h = makeHarness({ nodes: ovalNodes(26) });
    Vehicle.throttle[h.car] = 1;
    h.step(2);
    const after2s = Vehicle.speed[h.car];
    expect(after2s).toBeGreaterThan(15);
    h.step(12);
    const top = Vehicle.speed[h.car];
    expect(top).toBeGreaterThan(40);
    expect(top).toBeLessThanOrEqual(Vehicle.maxSpeed[h.car] + 0.5);
    // It should still be moving fast after another stretch.
    h.step(10);
    expect(Vehicle.speed[h.car]).toBeGreaterThan(25);
  });

  it('brakes to a stop, then reverses when the brake is held', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(4);
    expect(Vehicle.speed[h.car]).toBeGreaterThan(20);

    Vehicle.throttle[h.car] = 0;
    Vehicle.brakeInput[h.car] = 1;
    h.step(1.5);
    expect(Vehicle.speed[h.car]).toBeLessThanOrEqual(0.2);
    h.step(2);
    expect(Vehicle.speed[h.car]).toBeLessThan(-2);
    expect(Vehicle.speed[h.car]).toBeGreaterThanOrEqual(
      -Vehicle.reverseSpeed[h.car] - 0.1
    );
  });

  it('coasts to a standstill with no input', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(3);
    Vehicle.throttle[h.car] = 0;
    h.step(20);
    expect(Math.abs(Vehicle.speed[h.car])).toBeLessThan(0.5);
  });

  it('is slower off the racing surface', () => {
    // Note: each harness builds a fresh State, whose entity ids start again
    // from zero — so both cars land in the *same* SOA slot. Read each result
    // before building the next harness.
    const onTrack = makeHarness();
    Vehicle.throttle[onTrack.car] = 1;
    onTrack.step(6);
    const onTrackSpeed = Vehicle.speed[onTrack.car];
    const onTrackGrip = Vehicle.surfaceGrip[onTrack.car];

    const offTrack = makeHarness();
    // Park it out on the gravel beyond the road edge, then floor it.
    placeVehicleOnTrack(offTrack.car, offTrack.spline, 0, 10.5);
    Vehicle.throttle[offTrack.car] = 1;
    offTrack.step(6);
    const offTrackSpeed = Vehicle.speed[offTrack.car];
    const offTrackGrip = Vehicle.surfaceGrip[offTrack.car];

    expect(onTrackGrip).toBeGreaterThan(0.9);
    expect(offTrackGrip).toBeLessThan(0.7);
    expect(offTrackSpeed).toBeLessThan(onTrackSpeed);
  });
});

describe('vehicle: steering and drift', () => {
  it('turns right on positive steer and left on negative', () => {
    const right = makeHarness();
    Vehicle.throttle[right.car] = 1;
    right.step(1);
    const heading0 = Vehicle.heading[right.car];
    Vehicle.steerInput[right.car] = 1;
    right.step(1);
    expect(Vehicle.heading[right.car]).toBeGreaterThan(heading0);

    const left = makeHarness();
    Vehicle.throttle[left.car] = 1;
    left.step(1);
    const leftHeading0 = Vehicle.heading[left.car];
    Vehicle.steerInput[left.car] = -1;
    left.step(1);
    expect(Vehicle.heading[left.car]).toBeLessThan(leftHeading0);
  });

  it('steers less at speed than from a standstill', () => {
    const slow = makeHarness();
    // Feather the throttle so this car is genuinely crawling.
    Vehicle.throttle[slow.car] = 0.25;
    slow.step(1.2);
    Vehicle.steerInput[slow.car] = 1;
    slow.step(0.6);
    const slowYaw = Math.abs(Vehicle.yawRate[slow.car]);

    const fast = makeHarness();
    Vehicle.throttle[fast.car] = 1;
    fast.step(12);
    Vehicle.steerInput[fast.car] = 1;
    fast.step(0.6);
    const fastYaw = Math.abs(Vehicle.yawRate[fast.car]);

    expect(fastYaw).toBeLessThan(slowYaw);
  });

  it('slides more with the handbrake than without, and recovers when released', () => {
    // Under the drift model the handbrake doesn't raise the *peak* slip — it
    // makes the slide persist (grip recovers ~3x slower). Unwind the wheel
    // and compare a beat later: gripped hooks up, handbrake keeps sliding.
    // Wall-free so the arcs never become a barrier grind.
    const gripped = makeHarness({ walls: false });
    Vehicle.throttle[gripped.car] = 1;
    gripped.step(1);
    Vehicle.steerInput[gripped.car] = 1;
    gripped.step(0.8);
    Vehicle.steerInput[gripped.car] = 0;
    gripped.step(0.5);
    const grippedSlip = Vehicle.slip[gripped.car];

    const sliding = makeHarness({ walls: false });
    Vehicle.throttle[sliding.car] = 1;
    sliding.step(1);
    Vehicle.steerInput[sliding.car] = 1;
    Vehicle.handbrake[sliding.car] = 1;
    sliding.step(0.8);
    Vehicle.steerInput[sliding.car] = 0;
    sliding.step(0.5);
    const slidingSlip = Vehicle.slip[sliding.car];

    expect(slidingSlip).toBeGreaterThan(grippedSlip * 2);
    expect(slidingSlip).toBeGreaterThan(0.15);

    // Let go of everything and the tyres hook back up.
    Vehicle.handbrake[sliding.car] = 0;
    sliding.step(2);
    expect(Vehicle.slip[sliding.car]).toBeLessThan(slidingSlip);
  });

  it('does not spin on the spot when parked', () => {
    const h = makeHarness();
    const heading0 = Vehicle.heading[h.car];
    Vehicle.steerInput[h.car] = 1;
    h.step(2);
    expect(Math.abs(Vehicle.heading[h.car] - heading0)).toBeLessThan(0.05);
  });
});

describe('vehicle: track containment', () => {
  it('is stopped by the barrier and cannot be pushed through it', () => {
    const h = makeHarness({ nodes: tightNodes(), shoulder: 3 });
    // The wall now has thickness and the collision is inset by the car's half
    // width, so the centre of the car can only reach the shoulder edge plus the
    // wall thickness minus half the car width.
    const WALL_THICKNESS = 0.9;
    const carHalfWidth = Vehicle.halfWidth[h.car] || 0.85;
    const limit = 16 / 2 + 3 + WALL_THICKNESS - carHalfWidth;
    Vehicle.throttle[h.car] = 1;
    h.step(5);
    // Aim hard at the wall for a long time.
    Vehicle.steerInput[h.car] = 1;
    h.step(12);
    expect(Math.abs(Vehicle.trackLateral[h.car])).toBeLessThanOrEqual(
      limit + 1e-3
    );
  });

  it('scrubs speed when it hits the wall square on', () => {
    const h = makeHarness({ nodes: tightNodes() });
    Vehicle.throttle[h.car] = 1;
    h.step(8);
    const before = Vehicle.speed[h.car];
    Vehicle.steerInput[h.car] = 1;
    h.step(3);
    expect(Vehicle.speed[h.car]).toBeLessThan(before);
    expect(Vehicle.impactTimer[h.car]).toBeLessThan(3);
  });

  it('lets the car run wide when the track has no walls', () => {
    const h = makeHarness({ nodes: tightNodes(), walls: false });
    Vehicle.throttle[h.car] = 1;
    h.step(5);
    Vehicle.steerInput[h.car] = 1;
    h.step(6);
    expect(Math.abs(Vehicle.trackLateral[h.car])).toBeGreaterThan(16 / 2 + 3);
  });

  it('bounces off a registered track-side obstacle', () => {
    const h = makeHarness();
    const ahead = h.spline.positionAt(60, 0);
    addTrackObstacle(ahead.x, ahead.z, 4, 0.4);
    Vehicle.throttle[h.car] = 1;
    h.step(4);
    // It cannot have driven through the obstacle's centre.
    const pos = h.spline.positionAt(
      Vehicle.trackS[h.car],
      Vehicle.trackLateral[h.car]
    );
    const distance = Math.hypot(pos.x - ahead.x, pos.z - ahead.z);
    const passed = h.spline.deltaS(Vehicle.trackS[h.car], 60) > 0;
    expect(passed ? true : distance).not.toBe(0);
    expect(Vehicle.impactTimer[h.car]).toBeLessThan(5);
  });
});

describe('vehicle: grounding', () => {
  it('rides at its ride height on a flat track', () => {
    const h = makeHarness();
    Vehicle.throttle[h.car] = 1;
    h.step(4);
    expect(Vehicle.airHeight[h.car]).toBeCloseTo(Vehicle.rideHeight[h.car], 2);
    expect(Vehicle.airborne[h.car]).toBe(0);
    const proj = h.spline.project(
      Transform.posX[h.car],
      Transform.posY[h.car],
      Transform.posZ[h.car],
      Vehicle.trackS[h.car]
    );
    expect(proj.height).toBeCloseTo(Vehicle.rideHeight[h.car], 2);
  });

  it('follows the road up a hill instead of flying off it', () => {
    const nodes: TrackNode[] = [
      { x: -200, y: 0, z: -80 },
      { x: 0, y: 6, z: -80 },
      { x: 200, y: 12, z: -80 },
      { x: 260, y: 12, z: 0 },
      { x: 200, y: 8, z: 80 },
      { x: 0, y: 4, z: 80 },
      { x: -200, y: 0, z: 80 },
      { x: -260, y: 0, z: 0 },
    ];
    const h = makeHarness({ nodes });
    Vehicle.throttle[h.car] = 1;
    h.step(6);
    const proj = h.spline.project(
      Transform.posX[h.car],
      Transform.posY[h.car],
      Transform.posZ[h.car],
      Vehicle.trackS[h.car]
    );
    // Still glued to the surface within the ride height.
    expect(Math.abs(proj.height - Vehicle.rideHeight[h.car])).toBeLessThan(0.6);
  });

  it('gets airborne over a sharp crest at speed and lands again', () => {
    const nodes: TrackNode[] = [
      { x: -200, y: 0, z: -80 },
      { x: -40, y: 0, z: -80 },
      { x: 0, y: 9, z: -80 },
      { x: 40, y: 0, z: -80 },
      { x: 200, y: 0, z: -80 },
      { x: 260, y: 0, z: 0 },
      { x: 0, y: 0, z: 80 },
      { x: -260, y: 0, z: 0 },
    ];
    const h = makeHarness({ nodes });
    Vehicle.throttle[h.car] = 1;
    let sawAir = false;
    for (let i = 0; i < 60 * 12; i++) {
      VehicleControlSystem.update?.(h.state);
      if (Vehicle.airborne[h.car] === 1) sawAir = true;
    }
    expect(sawAir).toBe(true);
    // And it is back on the ground by the end of the run.
    expect(Vehicle.airborne[h.car]).toBe(0);
  });
});

describe('vehicle: race gating', () => {
  it('ignores the throttle until the lights go out', () => {
    const h = makeHarness();
    setRaceState({ phase: 'countdown', countdown: 3 });
    Vehicle.throttle[h.car] = 1;
    h.step(2);
    expect(Vehicle.speed[h.car]).toBe(0);
    expect(getRaceState().phase).toBe('countdown');
  });
});

describe('vehicle: slipstream', () => {
  it('gains speed when drafting a car ahead in the same lane', () => {
    const solo = makeHarness();
    Vehicle.throttle[solo.car] = 1;
    solo.step(1.5);
    const soloSpeed = Vehicle.speed[solo.car];

    const pack = makeHarness();
    const lead = pack.state.createEntity();
    pack.state.addComponent(lead, Transform);
    pack.state.addComponent(lead, WorldTransform);
    pack.state.addComponent(lead, Vehicle);
    pack.state.addComponent(lead, RaceTracker);
    Vehicle.maxSpeed[lead] = 50;
    Vehicle.accel[lead] = 26;
    Vehicle.brake[lead] = 48;
    Vehicle.engineBrake[lead] = 7;
    Vehicle.reverseSpeed[lead] = 12;
    Vehicle.maxSteer[lead] = 2.6;
    Vehicle.steerSpeed[lead] = 10;
    Vehicle.grip[lead] = 7;
    Vehicle.driftGrip[lead] = 0.32;
    Vehicle.halfLength[lead] = 1.35;
    Vehicle.halfWidth[lead] = 0.85;
    Vehicle.rideHeight[lead] = 0.35;
    placeVehicleOnTrack(lead, pack.spline, 8, 0);
    Vehicle.throttle[pack.car] = 1;
    Vehicle.throttle[lead] = 1;
    pack.step(1.5);

    expect(Vehicle.draft[pack.car]).toBeGreaterThan(0.2);
    expect(Vehicle.speed[pack.car]).toBeGreaterThan(soloSpeed + 0.4);
  });

  it('does not draft a car in another lane', () => {
    const pack = makeHarness();
    const lead = pack.state.createEntity();
    pack.state.addComponent(lead, Transform);
    pack.state.addComponent(lead, WorldTransform);
    pack.state.addComponent(lead, Vehicle);
    pack.state.addComponent(lead, RaceTracker);
    Vehicle.maxSpeed[lead] = 50;
    Vehicle.accel[lead] = 26;
    Vehicle.brake[lead] = 48;
    Vehicle.engineBrake[lead] = 7;
    Vehicle.reverseSpeed[lead] = 12;
    Vehicle.maxSteer[lead] = 2.6;
    Vehicle.steerSpeed[lead] = 10;
    Vehicle.grip[lead] = 7;
    Vehicle.driftGrip[lead] = 0.32;
    Vehicle.halfLength[lead] = 1.35;
    Vehicle.halfWidth[lead] = 0.85;
    Vehicle.rideHeight[lead] = 0.35;
    placeVehicleOnTrack(lead, pack.spline, 8, 6);
    Vehicle.throttle[pack.car] = 1;
    Vehicle.throttle[lead] = 1;
    pack.step(0.5);
    expect(Vehicle.draft[pack.car]).toBe(0);
  });
});

describe('vehicle: wet track', () => {
  it('is slower when the condition is wet', () => {
    setRaceState({ phase: 'racing', condition: 'dry' });
    const dry = makeHarness();
    Vehicle.throttle[dry.car] = 1;
    dry.step(3);
    const drySpeed = Vehicle.speed[dry.car];

    resetRaceState();
    clearTrackData();
    setRaceState({ phase: 'racing', condition: 'wet' });
    const wet = makeHarness();
    Vehicle.throttle[wet.car] = 1;
    wet.step(3);
    expect(Vehicle.speed[wet.car]).toBeLessThan(drySpeed * 0.98);
  });
});

describe('vehicle: keyboard bindings', () => {
  // readPlayerInput reads the global keyboard, so these tests drive the input
  // plugin's own handlers. A focused canvas is all handleKeyDown requires —
  // no DOM listeners are involved.
  const press = (code: string): void => {
    handleKeyDown({ code, preventDefault: () => {} } as KeyboardEvent);
  };
  const release = (code: string): void => {
    handleKeyUp({ code, preventDefault: () => {} } as KeyboardEvent);
  };

  afterEach(() => {
    clearAllInput();
    setFocusedCanvas(null);
  });

  it('reserves the home row (HJKL) for commands — it never steers', () => {
    setFocusedCanvas({} as HTMLCanvasElement);
    const h = makeHarness({ player: true, nodes: ovalNodes(26) });

    // The home row is the command cluster (J item, H horn, K/L future
    // actions): pressing any of it mid-drive must not touch the driver
    // inputs, or a fired power-up would wrench the wheel.
    for (const code of ['KeyH', 'KeyJ', 'KeyK', 'KeyL']) press(code);
    h.step(0.3);
    expect(Vehicle.throttle[h.car]).toBe(0);
    expect(Vehicle.brakeInput[h.car]).toBe(0);
    expect(Vehicle.steerInput[h.car]).toBe(0);
    expect(Vehicle.speed[h.car]).toBe(0);

    for (const code of ['KeyH', 'KeyJ', 'KeyK', 'KeyL']) release(code);
  });

  it('still drives with WASD and the arrow keys', () => {
    setFocusedCanvas({} as HTMLCanvasElement);
    const h = makeHarness({ player: true, nodes: ovalNodes(26) });

    press('ArrowUp');
    h.step(1);
    expect(Vehicle.throttle[h.car]).toBe(1);
    release('ArrowUp');

    press('KeyW');
    h.step(0.5);
    expect(Vehicle.throttle[h.car]).toBe(1);
    release('KeyW');

    press('KeyA');
    h.step(0.2);
    expect(Vehicle.steerInput[h.car]).toBe(1);
    release('KeyA');

    press('KeyD');
    h.step(0.2);
    expect(Vehicle.steerInput[h.car]).toBe(-1);
    release('KeyD');
  });
});
