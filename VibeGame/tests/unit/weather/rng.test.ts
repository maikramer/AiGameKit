import { describe, expect, it } from 'bun:test';
import { mulberry32, rngForSeed } from '../../../src/plugins/weather/rng';

describe('Weather seeded RNG', () => {
  describe('mulberry32', () => {
    it('produces values in [0, 1)', () => {
      const rng = mulberry32(12345);
      for (let i = 0; i < 1000; i++) {
        const v = rng();
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThan(1);
      }
    });

    it('is deterministic for the same seed', () => {
      const a = mulberry32(42);
      const b = mulberry32(42);
      const seqA = Array.from({ length: 10 }, () => a());
      const seqB = Array.from({ length: 10 }, () => b());
      expect(seqA).toEqual(seqB);
    });

    it('differs for different seeds', () => {
      const a = mulberry32(1);
      const b = mulberry32(2);
      const seqA = Array.from({ length: 10 }, () => a());
      const seqB = Array.from({ length: 10 }, () => b());
      expect(seqA).not.toEqual(seqB);
    });
  });

  describe('mulberry32 seed sweep', () => {
    for (let seed = 1; seed <= 80; seed++) {
      it(`seed ${seed} first draw is in [0, 1)`, () => {
        const v = mulberry32(seed)();
        expect(v).toBeGreaterThanOrEqual(0);
        expect(v).toBeLessThan(1);
      });
    }
  });

  describe('rngForSeed', () => {
    it('returns Math.random when seed is 0 (backward compat)', () => {
      // Math.random is the default random source; identity comparison is the
      // strongest assertion we can make without stubbing globals.
      expect(rngForSeed(0)).toBe(Math.random);
    });

    it('returns a seeded PRNG when seed is non-zero', () => {
      const rng = rngForSeed(999);
      expect(rng).not.toBe(Math.random);
      // Same seed → reproducible sequence.
      const again = rngForSeed(999);
      const s1 = Array.from({ length: 5 }, () => rng());
      const s2 = Array.from({ length: 5 }, () => again());
      expect(s1).toEqual(s2);
    });
  });
});
