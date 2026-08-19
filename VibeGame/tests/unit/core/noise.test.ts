import { describe, expect, it } from 'bun:test';
import { fbm2, valueNoise2 } from 'vibegame';

describe('noise (core/math)', () => {
  describe('valueNoise2', () => {
    it('is deterministic for the same x, z and seed', () => {
      expect(valueNoise2(12.3, -4.5, 7)).toBe(valueNoise2(12.3, -4.5, 7));
    });

    it('varies with the seed', () => {
      const a = valueNoise2(3.2, 8.9, 1);
      const b = valueNoise2(3.2, 8.9, 2);
      expect(a).not.toBe(b);
    });

    it('stays in [0, 1)', () => {
      for (let i = 0; i < 500; i++) {
        const x = ((i * 37.7) % 200) - 100;
        const z = ((i * -19.3) % 200) - 100;
        const n = valueNoise2(x, z, 42);
        expect(n).toBeGreaterThanOrEqual(0);
        expect(n).toBeLessThan(1);
      }
    });

    it('interpolates smoothly between lattice points', () => {
      // Along one cell the value is a smooth blend — neighbouring samples
      // close in input stay close in output (no white-noise jumps).
      const a = valueNoise2(10.0, 10.0, 5);
      const b = valueNoise2(10.05, 10.0, 5);
      expect(Math.abs(a - b)).toBeLessThan(0.05);
    });
  });

  describe('fbm2', () => {
    it('is deterministic', () => {
      expect(fbm2(4.4, 5.5, 9, 3)).toBe(fbm2(4.4, 5.5, 9, 3));
    });

    it('stays in [0, 1)', () => {
      for (let i = 0; i < 500; i++) {
        const n = fbm2(i * 2.3, i * -7.1, 13, 4);
        expect(n).toBeGreaterThanOrEqual(0);
        expect(n).toBeLessThan(1);
      }
    });

    it('clamps octaves instead of drifting with large values', () => {
      expect(fbm2(1, 1, 3, 99)).toBe(fbm2(1, 1, 3, 6));
      expect(fbm2(1, 1, 3, 0)).toBe(fbm2(1, 1, 3, 1));
    });

    it('averages roughly mid-range across a field (no collapse to edges)', () => {
      let sum = 0;
      const n = 400;
      for (let i = 0; i < n; i++) {
        sum += fbm2(i * 0.37, i * -0.61, 21, 3);
      }
      const avg = sum / n;
      expect(avg).toBeGreaterThan(0.2);
      expect(avg).toBeLessThan(0.8);
    });
  });
});
