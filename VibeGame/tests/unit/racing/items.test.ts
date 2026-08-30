import { afterEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { Transform, WorldTransform } from '../../../src/plugins/transforms';
import {
  HeldItem,
  ItemBox,
  ItemKind,
  PlayerVehicle,
  RaceTracker,
  Track,
  Vehicle,
} from '../../../src/plugins/racing/components';
import {
  attachTrackSpline,
  clearItemBoxes,
  clearOilSlicks,
  clearTrackData,
  getItemBoxes,
  getOilSlicks,
  addItemBox,
} from '../../../src/plugins/racing/data';
import {
  TrackSpline,
  type TrackNode,
} from '../../../src/plugins/racing/spline';
import {
  VehicleControlSystem,
  placeVehicleOnTrack,
} from '../../../src/plugins/racing/vehicle-control';
import { ItemBoxSystem } from '../../../src/plugins/racing/item-boxes';
import {
  ItemSystem,
  rollItem,
  useHeldItem,
} from '../../../src/plugins/racing/items';
import {
  resetRaceState,
  setRaceState,
} from '../../../src/plugins/racing/race-state';
import {
  clearAllInput,
  handleKeyDown,
  setFocusedCanvas,
} from '../../../src/plugins/input/utils';

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
      for (let i = 0; i < steps; i++) {
        VehicleControlSystem.update?.(state);
        ItemSystem.update?.(state);
      }
    },
  };
}

afterEach(() => {
  resetRaceState();
  clearTrackData();
  clearOilSlicks();
  clearItemBoxes();
  clearAllInput();
  setFocusedCanvas(null);
});

describe('item boxes', () => {
  it('collecting a box starts the roulette, which rolls into the slot', () => {
    const h = makeHarness();
    placeVehicleOnTrack(h.car, h.spline, 300, 0);
    // Park the kart right on top of a chest (world position from the spline,
    // as the transforms system would write it).
    addItemBox(300, 0, 5);
    const p = h.spline.positionAt(300, 0, 1.0);
    WorldTransform.posX[h.car] = p.x;
    WorldTransform.posY[h.car] = p.y;
    WorldTransform.posZ[h.car] = p.z;

    ItemBoxSystem.update?.(h.state);
    expect(HeldItem.rouletteTimer[h.car]).toBeGreaterThan(0);
    // The box despawns while it cools down.
    const box = getItemBoxes()[0]!;
    expect(box.eid).toBeGreaterThanOrEqual(0);
    expect(ItemBox.ttl[box.eid]).toBeLessThan(0);

    // ItemSystem ticks the roulette down and lands an item in the slot.
    const mutableTime = h.state.time as { elapsed: number };
    for (let i = 0; i < 90; i++) {
      mutableTime.elapsed = i * FIXED_DT;
      ItemSystem.update?.(h.state);
    }
    expect(HeldItem.rouletteTimer[h.car]).toBe(0);
    expect(HeldItem.item[h.car]).toBeGreaterThanOrEqual(ItemKind.Turbo);
  });

  it('a kart already holding an item does not waste the box', () => {
    const h = makeHarness();
    placeVehicleOnTrack(h.car, h.spline, 300, 0);
    HeldItem.item[h.car] = ItemKind.Shield;
    addItemBox(300, 0, 5);
    const p = h.spline.positionAt(300, 0, 1.0);
    WorldTransform.posX[h.car] = p.x;
    WorldTransform.posY[h.car] = p.y;
    WorldTransform.posZ[h.car] = p.z;

    ItemBoxSystem.update?.(h.state);
    expect(HeldItem.rouletteTimer[h.car]).toBe(0);
    // Box still live for the next kart.
    ItemBoxSystem.update?.(h.state);
    HeldItem.item[h.car] = ItemKind.None;
    ItemBoxSystem.update?.(h.state);
    expect(HeldItem.rouletteTimer[h.car]).toBeGreaterThan(0);
  });
});

describe('item roulette', () => {
  it('favours defence for the leader and offence for the stragglers', () => {
    let seed = 12345;
    const rand = (): number => {
      // Deterministic LCG so the statistics are stable in CI.
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    };
    const counts = (position: number): number[] => {
      const out = [0, 0, 0, 0, 0];
      for (let i = 0; i < 4000; i++) out[rollItem(position, 5, rand)]!++;
      return out;
    };
    const leader = counts(1);
    const last = counts(5);
    // Leader: oil + shield dominate turbo + fireball.
    expect(leader[ItemKind.Oil]! + leader[ItemKind.Shield]!).toBeGreaterThan(
      leader[ItemKind.Turbo]! + leader[ItemKind.Fireball]!
    );
    // Last place: turbo + fireball dominate.
    expect(last[ItemKind.Turbo]! + last[ItemKind.Fireball]!).toBeGreaterThan(
      last[ItemKind.Oil]! + last[ItemKind.Shield]!
    );
  });

  it('never rolls an empty slot', () => {
    for (let i = 0; i < 100; i++) {
      expect(rollItem(1 + (i % 5), 5, Math.random)).toBeGreaterThanOrEqual(
        ItemKind.Turbo
      );
    }
  });
});

describe('held items', () => {
  it('turbo fires once and clears the slot', () => {
    const h = makeHarness();
    HeldItem.item[h.car] = ItemKind.Turbo;
    expect(useHeldItem(h.car, h.spline)).toBe(true);
    expect(HeldItem.item[h.car]).toBe(ItemKind.None);
    expect(HeldItem.turboTime[h.car]).toBeGreaterThan(0);
    expect(useHeldItem(h.car, h.spline)).toBe(false);
  });

  it('fires the held item from the home row (J), not just Digit1', () => {
    setFocusedCanvas({} as HTMLCanvasElement);
    const h = makeHarness();
    // The fire-key edge latch is module state — a previous test that ended
    // with the key held would swallow this one's first press.
    ItemSystem.dispose?.(h.state);
    HeldItem.item[h.car] = ItemKind.Turbo;

    handleKeyDown({ code: 'KeyJ', preventDefault: () => {} } as KeyboardEvent);
    h.step(FIXED_DT);
    // J must consume the slot exactly like the number-row key — firing a
    // power-up should never lift a finger off WASD.
    expect(HeldItem.item[h.car]).toBe(ItemKind.None);
    expect(HeldItem.turboTime[h.car]).toBeGreaterThan(0);

    // Holding J does not double-fire: the burst counts down, the slot stays
    // empty.
    const burst = HeldItem.turboTime[h.car]!;
    h.step(FIXED_DT);
    expect(HeldItem.item[h.car]).toBe(ItemKind.None);
    expect(HeldItem.turboTime[h.car]).toBeLessThan(burst);
  });

  it('still fires the held item from Digit1', () => {
    setFocusedCanvas({} as HTMLCanvasElement);
    const h = makeHarness();
    ItemSystem.dispose?.(h.state);
    HeldItem.item[h.car] = ItemKind.Turbo;

    handleKeyDown({
      code: 'Digit1',
      preventDefault: () => {},
    } as KeyboardEvent);
    h.step(FIXED_DT);
    expect(HeldItem.item[h.car]).toBe(ItemKind.None);
    expect(HeldItem.turboTime[h.car]).toBeGreaterThan(0);
  });

  it('oil drops a slick behind that spins the first kart over it', () => {
    const h = makeHarness();
    placeVehicleOnTrack(h.car, h.spline, 200, 0);
    Vehicle.trackS[h.car] = 200;
    HeldItem.item[h.car] = ItemKind.Oil;
    expect(useHeldItem(h.car, h.spline)).toBe(true);
    expect(getOilSlicks().length).toBe(1);
    // The slick lands ~4 m behind the dropper, who is immune while it is fresh.
    expect(h.spline.deltaS(getOilSlicks()[0]!.s, 200)).toBeLessThan(0);

    // A second kart drives over it and spins.
    const victim = h.state.createEntity();
    h.state.addComponent(victim, Transform);
    h.state.addComponent(victim, WorldTransform);
    h.state.addComponent(victim, Vehicle);
    h.state.addComponent(victim, RaceTracker);
    h.state.addComponent(victim, HeldItem);
    placeVehicleOnTrack(victim, h.spline, getOilSlicks()[0]!.s, 0);
    h.step(0.1);
    expect(Vehicle.spinOutTimer[victim]).toBeGreaterThan(0);
    expect(getOilSlicks().length).toBe(0);
  });
});
