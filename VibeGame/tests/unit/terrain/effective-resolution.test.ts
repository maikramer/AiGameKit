import { describe, expect, it } from 'bun:test';
import {
  effectiveResolution,
  resolutionForLevel,
} from '../../../src/plugins/terrain/lod-select';

describe('effectiveResolution', () => {
  const base = 64;

  it('matches resolutionForLevel exactly when boost is 0 (retrocompat contract)', () => {
    for (const level of [0, 1, 2, 3, 4, 5]) {
      expect(effectiveResolution(base, level, 0)).toBe(
        resolutionForLevel(base, level)
      );
    }
  });

  it('matches resolutionForLevel when boost is negative', () => {
    for (const level of [0, 2, 5]) {
      expect(effectiveResolution(base, level, -1)).toBe(
        resolutionForLevel(base, level)
      );
    }
  });

  it('doubles LOD resolution at max boost (255), capped at baseResolution', () => {
    // LOD level 5: lodRes = max(4, 64 >> 5) = 4 → factor 2 → 8.
    expect(effectiveResolution(base, 5, 255)).toBe(8);
    // LOD level 0: lodRes = 64 → boosted would be 128, capped at base 64.
    expect(effectiveResolution(base, 0, 255)).toBe(64);
  });

  it('clamps boost above 255 as if it were 255', () => {
    expect(effectiveResolution(base, 5, 999)).toBe(
      effectiveResolution(base, 5, 255)
    );
  });

  it('scales linearly for intermediate boost', () => {
    // lodRes at level 5 = 4. boost 128 → factor 1 + 128/255 ≈ 1.502 → round(4*1.502) = 6.
    expect(effectiveResolution(base, 5, 128)).toBe(6);
  });

  it('never exceeds baseResolution', () => {
    for (const level of [0, 1, 2, 3]) {
      for (const boost of [0, 64, 128, 200, 255]) {
        expect(effectiveResolution(base, level, boost)).toBeLessThanOrEqual(
          base
        );
      }
    }
  });

  it('never goes below resolutionForLevel', () => {
    for (const level of [0, 1, 2, 3, 4, 5]) {
      for (const boost of [0, 1, 50, 200, 255]) {
        expect(effectiveResolution(base, level, boost)).toBeGreaterThanOrEqual(
          resolutionForLevel(base, level)
        );
      }
    }
  });
});
