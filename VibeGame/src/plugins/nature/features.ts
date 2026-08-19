import type { State } from '../../core';
import { fbm2 } from '../../core/math/noise';
import { BiomeRegion, BIOME_TYPE_MONTANHA } from '../biomes/components';
import { findBiomeRegionAt } from '../biomes/parser';
import { sampleTerrainSurface, slopeAngleRad } from '../spawner/surface';
import { distanceToRoadAt } from '../terrain/brush-registry';
import { distanceToWaterAt, isPointOnWaterBank } from '../water/registry';
import type { SiteFeatures } from './rules';

/** Type names for `BiomeRegion.type` (0..4), used by `<Where biome="…">`. */
const BIOME_TYPE_NAMES = ['vale', 'floresta', 'deserto', 'pantano', 'montanha'];

function biomeTypeName(type: number): string | null {
  return type >= 0 && type <= BIOME_TYPE_MONTANHA
    ? BIOME_TYPE_NAMES[type]!
    : null;
}

export interface SiteFeatureOptions {
  /** World metres per noise cell — larger = broader species patches. */
  noiseScale: number;
  noiseSeed: number;
}

/**
 * Sample every site feature the `<Where>` rules can test, for one candidate
 * point. Returns null when the terrain has no surface there (outside every
 * terrain field) — the planner drops such candidates.
 */
export function sampleSiteFeatures(
  state: State,
  x: number,
  z: number,
  opts: SiteFeatureOptions
): SiteFeatures | null {
  const sample = sampleTerrainSurface(state, x, z, 0.75, false);
  if (!sample) return null;
  const region = findBiomeRegionAt(state, x, z);
  return {
    altitude: sample.worldY,
    slopeDeg: (slopeAngleRad(sample.normal) * 180) / Math.PI,
    biomeId: region ? region.id.toLowerCase() : null,
    biomeType: region ? biomeTypeName(BiomeRegion.type[region.entity]) : null,
    waterDist: distanceToWaterAt(state, x, z),
    roadDist: distanceToRoadAt(state, x, z),
    onBank: isPointOnWaterBank(state, x, z),
    noise: fbm2(x / opts.noiseScale, z / opts.noiseScale, opts.noiseSeed, 3),
  };
}
