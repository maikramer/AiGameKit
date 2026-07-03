import type { HeightSampler } from './height-sampler';

/** Axis-aligned bounding box in field-local world space (X/Z, metres). */
export interface WorldAabb {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/**
 * Coarse grid of per-region density boosts layered over a terrain field.
 * One entry per tile; value 0..255 indicates how much extra mesh resolution
 * the region deserves (consumed by `effectiveResolution` in lod-select).
 */
export interface DensityMap {
  tilesX: number;
  tilesZ: number;
  /** Row-major `tilesX*tilesZ` boost values, 0..255. */
  boost: Uint8Array;
  /** World extent the tiles span (matches sampler.worldSize). */
  worldSize: number;
}

export interface BuildDensityOptions {
  /** Weight of local height variance in the score (default 1.0). */
  varianceWeight?: number;
  /**
   * Normalized variance threshold above which a tile gets a non-zero boost
   * (default 0.02). Tiles below the threshold stay at 0.
   */
  threshold?: number;
}

/** Clamped tile column for a world X coordinate. */
function tileX(density: DensityMap, worldX: number): number {
  const n = (worldX + density.worldSize / 2) / density.worldSize;
  const t = Math.floor(n * density.tilesX);
  return t < 0 ? 0 : t >= density.tilesX ? density.tilesX - 1 : t;
}

/** Clamped tile row for a world Z coordinate. */
function tileZ(density: DensityMap, worldZ: number): number {
  const n = (worldZ + density.worldSize / 2) / density.worldSize;
  const t = Math.floor(n * density.tilesZ);
  return t < 0 ? 0 : t >= density.tilesZ ? density.tilesZ - 1 : t;
}

/**
 * Build a density map from a height sampler by scoring each tile's local
 * height variance. Flat regions score 0; featured regions (canyons, ridges,
 * lake beds) score high. The sampler is read-only and may be flat (then every
 * tile is 0).
 */
export function buildDensityMap(
  sampler: HeightSampler,
  tilesPerAxis = 64,
  opts: BuildDensityOptions = {}
): DensityMap {
  const varianceWeight = opts.varianceWeight ?? 1.0;
  const threshold = opts.threshold ?? 0.02;

  const tilesX = Math.max(1, Math.floor(tilesPerAxis));
  const tilesZ = tilesX;
  const boost = new Uint8Array(tilesX * tilesZ);

  const { data, width, height, worldSize } = sampler;
  if (!data || width < 2 || height < 2) {
    return { tilesX, tilesZ, boost, worldSize };
  }

  // 4×4 height probes per tile; variance accumulated in scalars (sum/sumSq)
  // so a 64×64 build allocates nothing per tile.
  const probes = 4;
  const probeCount = probes * probes;
  for (let tz = 0; tz < tilesZ; tz++) {
    for (let tx = 0; tx < tilesX; tx++) {
      let sum = 0;
      let sumSq = 0;
      for (let sz = 0; sz < probes; sz++) {
        const v = (tz + (sz + 0.5) / probes) / tilesZ;
        const gz = Math.min(height - 1, Math.floor(v * (height - 1)));
        for (let sx = 0; sx < probes; sx++) {
          const u = (tx + (sx + 0.5) / probes) / tilesX;
          const gx = Math.min(width - 1, Math.floor(u * (width - 1)));
          const h = data[gz * width + gx] ?? 0;
          sum += h;
          sumSq += h * h;
        }
      }
      const mean = sum / probeCount;
      const variance = Math.max(0, sumSq / probeCount - mean * mean);
      const score = variance * varianceWeight;
      if (score > threshold) {
        // Map (threshold, +∞) → (0, 255]; the slope is tuned so a metre-scale
        // step in normalized heights saturates the boost.
        boost[tz * tilesX + tx] = Math.min(
          255,
          Math.round((score - threshold) * 4000)
        );
      }
    }
  }
  return { tilesX, tilesZ, boost, worldSize };
}

/** Boost at a world point, clamped to the nearest edge tile. */
export function boostAt(
  density: DensityMap,
  worldX: number,
  worldZ: number
): number {
  return (
    density.boost[
      tileZ(density, worldZ) * density.tilesX + tileX(density, worldX)
    ] ?? 0
  );
}

/**
 * Force a minimum boost on every tile intersecting the AABB. Takes the max
 * with the existing value so multiple overrides compose safely (e.g. several
 * lakes, or a lake over a naturally featured region).
 */
export function applyOverride(
  density: DensityMap,
  aabb: WorldAabb,
  boost: number
): void {
  const clamped = Math.max(0, Math.min(255, Math.round(boost)));
  const minTx = tileX(density, aabb.minX);
  const maxTx = tileX(density, aabb.maxX);
  const minTz = tileZ(density, aabb.minZ);
  const maxTz = tileZ(density, aabb.maxZ);
  for (let tz = minTz; tz <= maxTz; tz++) {
    for (let tx = minTx; tx <= maxTx; tx++) {
      const i = tz * density.tilesX + tx;
      if (clamped > (density.boost[i] ?? 0)) density.boost[i] = clamped;
    }
  }
}

/**
 * Maximum boost over all tiles intersecting the AABB. Used by the terrain
 * LOD selection to pick a chunk's effective resolution: a chunk that merely
 * touches a featured region adopts that region's boost, avoiding intra-chunk
 * resolution cracks.
 */
export function maxBoostOverAabb(density: DensityMap, aabb: WorldAabb): number {
  const minTx = tileX(density, aabb.minX);
  const maxTx = tileX(density, aabb.maxX);
  const minTz = tileZ(density, aabb.minZ);
  const maxTz = tileZ(density, aabb.maxZ);
  let best = 0;
  for (let tz = minTz; tz <= maxTz; tz++) {
    const row = tz * density.tilesX;
    for (let tx = minTx; tx <= maxTx; tx++) {
      const v = density.boost[row + tx] ?? 0;
      if (v > best) {
        best = v;
        if (best === 255) return best;
      }
    }
  }
  return best;
}
