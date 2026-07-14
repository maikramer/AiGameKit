import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  clearGroundBrushes,
  getGroundBrushes,
  pointInPadCore,
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
});
