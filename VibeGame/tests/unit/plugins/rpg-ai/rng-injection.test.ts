import { afterEach, describe, expect, it } from 'bun:test';
import {
  aiRandom,
  createAiInstanceState,
  resetAiRng,
  setAiRng,
} from 'vibegame';

function makeLcg(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

describe('AI RNG injection / determinism (M7)', () => {
  afterEach(() => {
    resetAiRng();
  });

  it('createAiInstanceState yields identical strafeDir under a seeded RNG', () => {
    setAiRng(makeLcg(12345));
    const a = createAiInstanceState();

    setAiRng(makeLcg(12345));
    const b = createAiInstanceState();

    expect(a.strafeDir).toBe(b.strafeDir);
    expect(a.strafeDir === -1 || a.strafeDir === 1).toBe(true);
  });

  it('two different seeds can produce different strafeDir (sanity)', () => {
    setAiRng(makeLcg(12345));
    const a = createAiInstanceState();

    setAiRng(makeLcg(99999));
    const b = createAiInstanceState();

    expect(a.strafeDir === -1 || a.strafeDir === 1).toBe(true);
    expect(b.strafeDir === -1 || b.strafeDir === 1).toBe(true);
  });

  it('aiRandom produces an identical sequence when re-seeded to the same value', () => {
    setAiRng(makeLcg(12345));
    const seq1 = Array.from({ length: 10 }, () => aiRandom());

    setAiRng(makeLcg(12345));
    const seq2 = Array.from({ length: 10 }, () => aiRandom());

    expect(seq2).toEqual(seq1);

    for (const v of seq1) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThan(1);
    }
  });

  it('different seeds produce different aiRandom sequences', () => {
    setAiRng(makeLcg(12345));
    const seq1 = Array.from({ length: 10 }, () => aiRandom());

    setAiRng(makeLcg(98765));
    const seq2 = Array.from({ length: 10 }, () => aiRandom());

    expect(seq2).not.toEqual(seq1);
  });

  it('resetAiRng restores Math.random behaviour', () => {
    resetAiRng();
    const v = aiRandom();

    expect(typeof v).toBe('number');
    expect(v).toBeGreaterThanOrEqual(0);
    expect(v).toBeLessThan(1);
  });
});
