import { describe, expect, it } from 'bun:test';
import { computeWaterDrag } from '../../../src/plugins/water/effects';

describe('computeWaterDrag', () => {
  it('is zero at the surface and for negative submersion', () => {
    expect(computeWaterDrag(0)).toBe(0);
    expect(computeWaterDrag(-1)).toBe(0);
  });

  it('grows with submersion depth', () => {
    const shallow = computeWaterDrag(0.2);
    const waist = computeWaterDrag(0.6);
    expect(shallow).toBeGreaterThan(0);
    expect(waist).toBeGreaterThan(shallow);
  });

  it('caps at the max drag once fully submerged', () => {
    const full = computeWaterDrag(1.1);
    expect(computeWaterDrag(5)).toBe(full);
    expect(full).toBeLessThanOrEqual(0.9);
  });
});
