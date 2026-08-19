import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  distanceToWaterAt,
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

  describe('distanceToWaterAt', () => {
    it('null when no water bodies are registered', () => {
      expect(distanceToWaterAt(state, 0, 0)).toBeNull();
    });

    it('lake: 0 at the waterline, negative inside, positive on land', () => {
      registerWaterBody(state, {
        kind: 'lake',
        x: 10,
        z: 20,
        radius: 9,
        shoreRadius: 8,
        carveRadius: 10,
        waterY: 4,
      });
      expect(distanceToWaterAt(state, 10, 20)).toBeCloseTo(-8, 5);
      expect(distanceToWaterAt(state, 18, 20)).toBeCloseTo(0, 5);
      expect(distanceToWaterAt(state, 13, 20)).toBeCloseTo(-5, 5);
      expect(distanceToWaterAt(state, 25, 20)).toBeCloseTo(7, 5);
    });

    it('river: distance from the waterline channel, falls back to width', () => {
      registerWaterBody(state, {
        kind: 'river',
        path: [
          [0, 0],
          [100, 0],
        ],
        width: 6,
        waterY: 3,
      });
      // No shoreWidth → waterline half = width/2 = 3.
      expect(distanceToWaterAt(state, 50, 3)).toBeCloseTo(0, 5);
      expect(distanceToWaterAt(state, 50, 0)).toBeCloseTo(-3, 5);
      expect(distanceToWaterAt(state, 50, 9)).toBeCloseTo(6, 5);
    });

    it('returns the distance to the nearest of several bodies', () => {
      registerWaterBody(state, {
        kind: 'lake',
        x: 0,
        z: 0,
        radius: 5,
        shoreRadius: 4,
        waterY: 1,
      });
      registerWaterBody(state, {
        kind: 'lake',
        x: 30,
        z: 0,
        radius: 5,
        shoreRadius: 4,
        waterY: 1,
      });
      // Equidistant edges at x=4 and x=26; from x=20 the second lake wins.
      expect(distanceToWaterAt(state, 20, 0)).toBeCloseTo(6, 5);
    });
  });
});
