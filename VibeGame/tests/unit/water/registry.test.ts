import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  getWaterBodies,
  isPointInWater,
  registerWaterBody,
  unregisterWaterBody,
  waterBodyAt,
  waterLevelAt,
  type WaterBody,
} from '../../../src/plugins/water/registry';

describe('WaterBody registry', () => {
  let state: State;
  beforeEach(() => {
    state = new State();
  });

  describe('lake body', () => {
    const lake: WaterBody = {
      kind: 'lake',
      x: 10,
      z: 20,
      radius: 5,
      shoreRadius: 4,
      waterY: 8,
    };

    it('isPointInWater is true inside the disc', () => {
      registerWaterBody(state, lake);
      expect(isPointInWater(state, 10, 20)).toBe(true);
      expect(isPointInWater(state, 13, 20)).toBe(true); // within radius
      expect(isPointInWater(state, 16, 20)).toBe(false); // outside radius
    });

    it('waterLevelAt returns the surface height inside the disc', () => {
      registerWaterBody(state, lake);
      expect(waterLevelAt(state, 10, 20)).toBe(8);
      expect(waterLevelAt(state, 100, 100)).toBeNull();
    });

    it('waterBodyAt returns the body inside, null outside', () => {
      registerWaterBody(state, lake);
      expect(waterBodyAt(state, 10, 20)).toBe(lake);
      expect(waterBodyAt(state, 100, 100)).toBeNull();
    });
  });

  describe('river body', () => {
    // A river along +X from (0,0) to (100,0), width 6.
    const river: WaterBody = {
      kind: 'river',
      path: [
        [0, 0],
        [100, 0],
      ],
      width: 6,
      waterY: 3,
    };

    it('isPointInWater is true within width/2 of the path', () => {
      registerWaterBody(state, river);
      expect(isPointInWater(state, 50, 0)).toBe(true); // on the axis
      expect(isPointInWater(state, 50, 2)).toBe(true); // within width/2 = 3
      expect(isPointInWater(state, 50, 4)).toBe(false); // outside width/2
      expect(isPointInWater(state, -5, 0)).toBe(false); // past the source end
    });

    it('waterLevelAt returns the surface height inside the channel', () => {
      registerWaterBody(state, river);
      expect(waterLevelAt(state, 50, 1)).toBe(3);
      expect(waterLevelAt(state, 50, 10)).toBeNull();
    });
  });

  it('unregister removes the body so queries no longer see it', () => {
    const lake: WaterBody = {
      kind: 'lake',
      x: 0,
      z: 0,
      radius: 5,
      shoreRadius: 4,
      waterY: 1,
    };
    registerWaterBody(state, lake);
    expect(isPointInWater(state, 0, 0)).toBe(true);
    unregisterWaterBody(state, lake);
    expect(isPointInWater(state, 0, 0)).toBe(false);
    expect(getWaterBodies(state)).toHaveLength(0);
  });
});
