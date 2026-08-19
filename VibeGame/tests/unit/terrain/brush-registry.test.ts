import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  clearGroundBrushes,
  distanceToRoadAt,
  getGroundBrushes,
  isPointOnRoad,
  pointInPadCore,
  pointInAnyPadCore,
  pointInRoadCorridor,
  pointInRoadCarve,
  flyingDeckYAt,
  crownHitsFlyingDeck,
  registerGroundBrush,
  unregisterGroundBrush,
  type GroundBrush,
} from '../../../src/plugins/terrain/brush-registry';

describe('brush-registry', () => {
  it('register / get / clear are isolated per State', () => {
    const a = new State();
    const b = new State();
    const brush: GroundBrush = {
      kind: 'pad',
      minX: -10,
      maxX: 10,
      minZ: -10,
      maxZ: 10,
      targetY: 4,
      halfX: 8,
      halfZ: 8,
      cornerRadius: 2,
    };
    registerGroundBrush(a, brush);
    expect(getGroundBrushes(a)).toHaveLength(1);
    expect(getGroundBrushes(b)).toHaveLength(0);

    clearGroundBrushes(a);
    expect(getGroundBrushes(a)).toHaveLength(0);
  });

  it('unregister removes only the matching brush', () => {
    const state = new State();
    const pad: GroundBrush = {
      kind: 'pad',
      minX: -5,
      maxX: 5,
      minZ: -5,
      maxZ: 5,
      halfX: 4,
      halfZ: 4,
      targetY: 1,
    };
    const road: GroundBrush = {
      kind: 'road',
      minX: 0,
      maxX: 20,
      minZ: -2,
      maxZ: 2,
      halfWidth: 2,
      path: [0, 0, 20, 0],
    };
    registerGroundBrush(state, pad);
    registerGroundBrush(state, road);
    unregisterGroundBrush(state, pad);
    expect(getGroundBrushes(state)).toEqual([road]);
  });

  it('pointInPadCore is true inside rounded core and false in falloff ring', () => {
    const brush: GroundBrush = {
      kind: 'pad',
      // AABB = core±falloff (half 10 + falloff 5 → ±40)
      minX: -15,
      maxX: 15,
      minZ: -15,
      maxZ: 15,
      halfX: 10,
      halfZ: 10,
      cornerRadius: 2,
      targetY: 3,
    };
    expect(pointInPadCore(brush, 0, 0)).toBe(true);
    expect(pointInPadCore(brush, 8, 0)).toBe(true);
    // Outside core halfX but still inside AABB falloff ring
    expect(pointInPadCore(brush, 12, 0)).toBe(false);
    expect(pointInPadCore(brush, 20, 0)).toBe(false);
  });

  it('pointInAnyPadCore is true only inside a registered pad core', () => {
    const state = new State();
    registerGroundBrush(state, {
      kind: 'pad',
      minX: -15,
      maxX: 15,
      minZ: -15,
      maxZ: 15,
      halfX: 10,
      halfZ: 10,
      cornerRadius: 2,
      targetY: 3,
    });
    expect(pointInAnyPadCore(state, 0, 0)).toBe(true);
    expect(pointInAnyPadCore(state, 12, 0)).toBe(false);
    expect(pointInAnyPadCore(new State(), 0, 0)).toBe(false);
  });

  it('pointInRoadCorridor follows halfWidth along the path', () => {
    const brush: GroundBrush = {
      kind: 'road',
      minX: 0,
      maxX: 20,
      minZ: -3,
      maxZ: 3,
      halfWidth: 2.5,
      path: [0, 0, 20, 0],
    };
    expect(pointInRoadCorridor(brush, 10, 0)).toBe(true);
    expect(pointInRoadCorridor(brush, 10, 2)).toBe(true);
    expect(pointInRoadCorridor(brush, 10, 3)).toBe(false);
  });

  it('pointInRoadCarve uses carveHalfWidth; corridor stays on the bed', () => {
    const brush: GroundBrush = {
      kind: 'road',
      minX: 0,
      maxX: 20,
      minZ: -20,
      maxZ: 20,
      halfWidth: 4,
      carveHalfWidth: 16,
      path: [0, 0, 20, 0],
    };
    // On the talude: plantable, still on the carved shelf.
    expect(pointInRoadCorridor(brush, 10, 8)).toBe(false);
    expect(pointInRoadCarve(brush, 10, 8)).toBe(true);
    expect(pointInRoadCarve(brush, 10, 17)).toBe(false);
  });

  it('isPointOnRoad covers flatten-road corridor and plaza pad core', () => {
    const state = new State();
    registerGroundBrush(state, {
      kind: 'road',
      minX: 0,
      maxX: 20,
      minZ: -2,
      maxZ: 2,
      halfWidth: 2,
      path: [0, 0, 20, 0],
    });
    registerGroundBrush(state, {
      kind: 'pad',
      minX: -20,
      maxX: -10,
      minZ: -5,
      maxZ: 5,
      halfX: 4,
      halfZ: 4,
      cornerRadius: 0,
      targetY: 1,
    });
    expect(isPointOnRoad(state, 10, 0)).toBe(true);
    expect(isPointOnRoad(state, 10, 3)).toBe(false);
    expect(isPointOnRoad(state, -15, 0)).toBe(true);
    expect(isPointOnRoad(state, -15, 8)).toBe(false);
  });

  it('flying road is not paved; crownHitsFlyingDeck uses deck Y', () => {
    const state = new State();
    registerGroundBrush(state, {
      kind: 'road',
      minX: 0,
      maxX: 40,
      minZ: -8,
      maxZ: 8,
      halfWidth: 6,
      flying: true,
      path: [0, 0, 40, 0],
      pathY: [20, 22],
    });
    expect(isPointOnRoad(state, 20, 0)).toBe(false);
    const brush = getGroundBrushes(state)[0]!;
    expect(pointInRoadCorridor(brush, 20, 0)).toBe(false);
    expect(pointInRoadCarve(brush, 20, 0)).toBe(false);
    expect(flyingDeckYAt(brush, 20, 0)).toBeCloseTo(21, 5);
    expect(flyingDeckYAt(brush, 20, 10)).toBeNull();
    // Valley oak ~8 m + plant at y=8 → crown 16, deck 21 → stays.
    expect(crownHitsFlyingDeck(state, 20, 0, 16)).toBe(false);
    // Same tree at y=14 → crown 22, pierces deck.
    expect(crownHitsFlyingDeck(state, 20, 0, 22)).toBe(true);
    // Off the span: no cull.
    expect(crownHitsFlyingDeck(state, 20, 20, 40)).toBe(false);
  });

  describe('distanceToRoadAt', () => {
    it('null when no road brushes exist', () => {
      const state = new State();
      expect(distanceToRoadAt(state, 0, 0)).toBeNull();
    });

    it('signed distance to the carve edge (falls back to halfWidth)', () => {
      const state = new State();
      registerGroundBrush(state, {
        kind: 'road',
        minX: 0,
        maxX: 40,
        minZ: -5,
        maxZ: 5,
        halfWidth: 4,
        path: [0, 0, 40, 0],
      });
      // No carveHalfWidth → carve edge = bed edge (halfWidth 4).
      expect(distanceToRoadAt(state, 20, 0)).toBeCloseTo(-4, 5);
      expect(distanceToRoadAt(state, 20, 4)).toBeCloseTo(0, 5);
      expect(distanceToRoadAt(state, 20, 12)).toBeCloseTo(8, 5);
    });

    it('uses carveHalfWidth when present', () => {
      const state = new State();
      registerGroundBrush(state, {
        kind: 'road',
        minX: 0,
        maxX: 40,
        minZ: -10,
        maxZ: 10,
        halfWidth: 4,
        carveHalfWidth: 10,
        path: [0, 0, 40, 0],
      });
      expect(distanceToRoadAt(state, 20, 0)).toBeCloseTo(-10, 5);
      expect(distanceToRoadAt(state, 20, 10)).toBeCloseTo(0, 5);
      expect(distanceToRoadAt(state, 20, 16)).toBeCloseTo(6, 5);
    });

    it('ignores flying spans and keeps the nearest of several roads', () => {
      const state = new State();
      registerGroundBrush(state, {
        kind: 'road',
        minX: 0,
        maxX: 40,
        minZ: -8,
        maxZ: 8,
        halfWidth: 6,
        flying: true,
        path: [0, 0, 40, 0],
        pathY: [20, 22],
      });
      registerGroundBrush(state, {
        kind: 'road',
        minX: 0,
        maxX: 40,
        minZ: -5,
        maxZ: 5,
        halfWidth: 4,
        path: [0, 0, 40, 0],
      });
      expect(distanceToRoadAt(state, 20, 8)).toBeCloseTo(4, 5);
    });
  });
});
