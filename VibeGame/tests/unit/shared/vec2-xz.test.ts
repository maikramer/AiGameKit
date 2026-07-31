import { describe, expect, it } from 'bun:test';
import { vec2 } from '../../../src/shared';

describe('vec2.distanceXZ', () => {
  it('returns the ground-plane distance (ignores Y)', () => {
    expect(vec2.distanceXZ(0, 0, 3, 4)).toBeCloseTo(5, 10);
    expect(vec2.distanceXZ(10, 10, 10, 10)).toBe(0);
  });

  it('is symmetric', () => {
    const a = vec2.distanceXZ(1, 2, -3, 4);
    const b = vec2.distanceXZ(-3, 4, 1, 2);
    expect(a).toBeCloseTo(b, 10);
  });
});

describe('vec2.normalizeXZ', () => {
  it('normalizes a direction to unit length', () => {
    const n = vec2.normalizeXZ(3, 4);
    expect(n.x).toBeCloseTo(3 / 5, 10);
    expect(n.z).toBeCloseTo(4 / 5, 10);
  });

  it('keeps the zero vector zero', () => {
    expect(vec2.normalizeXZ(0, 0)).toEqual({ x: 0, z: 0 });
  });

  it('handles tiny magnitudes without division blowup', () => {
    const n = vec2.normalizeXZ(1e-12, 0);
    expect(n).toEqual({ x: 0, z: 0 });
  });
});
