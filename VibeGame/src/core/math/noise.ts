/**
 * Seeded deterministic 2D value noise + fBm.
 *
 * Same (x, z, seed) always yields the same value — planner decisions (species
 * patches, mask bands) stay stable across reloads.
 */

/** Integer lattice hash → [0, 1). imul mixing keeps it fast and seedable. */
function hash2(ix: number, iz: number, seed: number): number {
  let h =
    Math.imul(ix, 0x27d4eb2d) ^
    Math.imul(iz, 0x165667b1) ^
    Math.imul(seed, 0x9e3779b1);
  h = Math.imul(h ^ (h >>> 15), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

function smooth(t: number): number {
  return t * t * (3 - 2 * t);
}

/** Value noise at (x, z): bilinear blend of hashed lattice corners → [0, 1). */
export function valueNoise2(x: number, z: number, seed = 1): number {
  const ix = Math.floor(x);
  const iz = Math.floor(z);
  const fx = smooth(x - ix);
  const fz = smooth(z - iz);
  const c00 = hash2(ix, iz, seed);
  const c10 = hash2(ix + 1, iz, seed);
  const c01 = hash2(ix, iz + 1, seed);
  const c11 = hash2(ix + 1, iz + 1, seed);
  const a = c00 + (c10 - c00) * fx;
  const b = c01 + (c11 - c01) * fx;
  return a + (b - a) * fz;
}

/**
 * Fractal Brownian motion over `valueNoise2` → [0, 1).
 * `octaves` clamped to [1, 6]; each octave doubles frequency and halves
 * amplitude. Feeding world XZ divided by a scale (metres) gives organic
 * species patches.
 */
export function fbm2(x: number, z: number, seed = 1, octaves = 3): number {
  const n = Math.max(1, Math.min(6, Math.floor(octaves)));
  let sum = 0;
  let amp = 1;
  let total = 0;
  let fx = x;
  let fz = z;
  for (let o = 0; o < n; o++) {
    sum += valueNoise2(fx, fz, seed + o * 101) * amp;
    total += amp;
    amp *= 0.5;
    fx *= 2;
    fz *= 2;
  }
  return sum / total;
}
