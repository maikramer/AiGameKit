import type { XMLValue } from '../../core';

/**
 * Parsed rule model for `<NatureSpawner>`. The parser fills this once per
 * tag; the planner consumes it after the ground is ready. Pure data — every
 * function here is testable without ECS.
 */

/** Inclusive numeric band. `min`/`max` may be ±Infinity (open ends). */
export interface RangeBand {
  min: number;
  max: number;
}

export function inBand(value: number, band: RangeBand): boolean {
  return value >= band.min && value <= band.max;
}

/**
 * Band parser for `min..max` strings: `"2..16"`, `"18.."`, `"..6"`, `"5"`
 * (exact). Throws with a usage hint on malformed input.
 */
export function parseRangeBand(
  raw: XMLValue | undefined,
  label: string
): RangeBand | undefined {
  if (raw === undefined || raw === null) return undefined;
  const s = String(raw).trim();
  if (!s) return undefined;
  const m = s.match(/^(-?\d+(?:\.\d+)?)?\s*\.\.\s*(-?\d+(?:\.\d+)?)?$/);
  if (m) {
    const min = m[1] !== undefined ? parseFloat(m[1]) : -Infinity;
    const max = m[2] !== undefined ? parseFloat(m[2]) : Infinity;
    if (min > max) {
      throw new Error(
        `[nature] ${label}="${s}": min > max. Use "min..max", "min.." ou "..max".`
      );
    }
    return { min, max };
  }
  const single = parseFloat(s);
  if (!Number.isNaN(single)) return { min: single, max: single };
  throw new Error(
    `[nature] ${label}="${s}" inválido. Use "min..max", "min..", "..max" ou um valor exato.`
  );
}

/** `<Where>` conditions — all optional, ANDed at match time. */
export interface WhereCondition {
  /** Region ids or biome type names (vale/floresta/deserto/pantano/montanha). */
  biome?: string[];
  altitude?: RangeBand;
  slope?: RangeBand;
  /** `in` = floats on the wet surface; `bank` = carved beach/bank ring. */
  waterMode?: 'in' | 'bank';
  /** Signed distance (m) to the waterline; negative inside the water. */
  waterDist?: RangeBand;
  /** Signed distance (m) to the road carve edge; negative over the carve. */
  roadDist?: RangeBand;
  /** Species ids that must already have instances nearby. */
  nearSpecies?: string[];
  nearDist?: RangeBand;
  /** fBm mask [0,1) — organic species patches. */
  noise?: RangeBand;
}

export function hasNearCondition(where: WhereCondition): boolean {
  return where.nearSpecies !== undefined && where.nearSpecies.length > 0;
}

/**
 * Site features sampled once per candidate point and shared by every species
 * test. `null` distances mean the world has no water/roads — conditions on
 * them fail (the author asked for proximity to something that is not there).
 */
export interface SiteFeatures {
  altitude: number;
  slopeDeg: number;
  biomeId: string | null;
  biomeType: string | null;
  waterDist: number | null;
  roadDist: number | null;
  /** True on the carved bank/beach ring (inside carve, outside wet surface). */
  onBank: boolean;
  noise: number;
}

function bandOk(value: number | null, band: RangeBand | undefined): boolean {
  if (band === undefined) return true;
  if (value === null) return false;
  return inBand(value, band);
}

/**
 * Whether a site satisfies a `<Where>`. `nearDists` (species id → distance to
 * its nearest planned instance) is only provided by the planner's near phase;
 * without it any `near` condition fails.
 */
export function matchesWhere(
  where: WhereCondition,
  f: SiteFeatures,
  nearDists?: ReadonlyMap<string, number>
): boolean {
  if (where.biome !== undefined) {
    const ok = where.biome.some((b) => b === f.biomeId || b === f.biomeType);
    if (!ok) return false;
  }
  if (!bandOk(f.altitude, where.altitude)) return false;
  if (!bandOk(f.slopeDeg, where.slope)) return false;
  if (where.waterMode === 'in') {
    if (f.waterDist === null || f.waterDist > 0) return false;
  } else if (where.waterMode === 'bank') {
    if (!f.onBank) return false;
  }
  if (where.waterMode === undefined && !bandOk(f.waterDist, where.waterDist)) {
    return false;
  }
  if (!bandOk(f.roadDist, where.roadDist)) return false;
  if (!bandOk(f.noise, where.noise)) return false;
  if (hasNearCondition(where) && where.nearDist) {
    if (!nearDists) return false;
    const ok = where.nearSpecies!.some((sid) => {
      const d = nearDists.get(sid);
      return d !== undefined && inBand(d, where.nearDist!);
    });
    if (!ok) return false;
  }
  return true;
}

/** One spawnable asset plus its site rules. */
export interface SpeciesRule {
  id: string;
  /** Relative probability in the weighted pick; 0 = only spawned via groves. */
  weight: number;
  /** Max planned instances; 0 = unlimited. */
  cap: number;
  url: string;
  lod1Url?: string;
  lod2Url?: string;
  where: WhereCondition;
  /** Group profile id (`tree`, `none`, …) resolved at spec build. */
  profile: string;
  /** Visual/placement attributes forwarded to resolveGroupSpawnFields. */
  spawnAttrs: Record<string, XMLValue>;
}

export interface GroveMemberRule {
  species: string;
  countMin: number;
  countMax: number;
  /** Ring placement inside the grove: 0 = centre, 1 = edge. */
  ringMin: number;
  ringMax: number;
}

export interface GroveRule {
  id: string;
  count: number;
  radius: number;
  where: WhereCondition;
  members: GroveMemberRule[];
}

export interface NatureRulesPlan {
  seed: number;
  regionMin: [number, number, number];
  regionMax: [number, number, number];
  spawnCountMode: 'fixed' | 'density';
  count: number;
  densityPerKm2: number;
  minSpacing: number;
  noiseScale: number;
  species: SpeciesRule[];
  groves: GroveRule[];
}
