/**
 * Seeded RNG helpers for deterministic weather placement.
 *
 * Weather particle/instance placement (rain drops, cloud billboards) defaults
 * to `Math.random()`. When a `<Weather seed="N">` is declared, the placement
 * becomes reproducible: the same seed yields the same cloud field and rain
 * distribution across reloads, which matters for recorded sessions and visual
 * regression. The noise also gives spatially-coherent clustering (clouds/rain
 * group more naturally than uniform random).
 */

/**
 * Mulberry32 — a tiny, fast, well-distributed seeded PRNG. Returns a function
 * producing floats in [0, 1), a drop-in for `Math.random`.
 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Resolve a random source for a given weather seed. Returns `Math.random`
 * (the library default) when `seed` is falsy/0 — preserving backward
 * compatibility for scenes that don't declare a seed.
 */
export function rngForSeed(seed: number): () => number {
  return seed ? mulberry32(seed) : Math.random;
}
