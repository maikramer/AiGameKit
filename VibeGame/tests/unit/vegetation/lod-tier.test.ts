import { describe, expect, it } from 'bun:test';
import { vegetationLodTier } from '../../../src/plugins/vegetation/systems';

// Thresholds mirror systems.ts: LOD1 at 80u, LOD2 at 200u (compared squared).
const LOD1 = 80;
const LOD2 = 200;

describe('vegetationLodTier', () => {
  it('full detail (tier 0) inside the LOD1 ring', () => {
    expect(vegetationLodTier(0)).toBe(0);
    expect(vegetationLodTier((LOD1 - 1) ** 2)).toBe(0);
  });

  it('mid detail (tier 1) between LOD1 and LOD2', () => {
    expect(vegetationLodTier(LOD1 ** 2)).toBe(1);
    expect(vegetationLodTier((LOD1 + 10) ** 2)).toBe(1);
    expect(vegetationLodTier((LOD2 - 1) ** 2)).toBe(1);
  });

  it('far detail (tier 2) beyond LOD2', () => {
    expect(vegetationLodTier(LOD2 ** 2)).toBe(2);
    expect(vegetationLodTier((LOD2 + 500) ** 2)).toBe(2);
  });

  it('monotonic: tier never decreases as distance grows', () => {
    let prev = 0;
    for (let d = 0; d <= 400; d += 5) {
      const tier = vegetationLodTier(d * d);
      expect(tier).toBeGreaterThanOrEqual(prev);
      prev = tier;
    }
  });
});
