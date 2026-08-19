import type { XMLValue } from '../../core';
import { resolveVariationSpec } from '../spawn-variation';
import {
  normalizeGroupProfileId,
  resolveGroupSpawnFields,
} from '../spawner/profiles';
import type { SpawnGroupSpec } from '../spawner/types';
import type { NatureRulesPlan, SpeciesRule } from './rules';

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/**
 * Build the SpawnGroupSpec for one species' planned points. All visual /
 * placement fields come from the species attributes resolved through the
 * normal group profiles (`resolveGroupSpawnFields`), so `<Species>` accepts
 * the same attribute vocabulary as `<StaticSpawner>`.
 *
 * Rule-derived defaults:
 * - `maxSlopeDeg` = the `<Where slope>` upper bound (single source of truth).
 * - `avoid-water`/`avoid-road` default to false when the species has a
 *   water/road condition — the planner already placed those points relative
 *   to that feature, and the spawner's blanket rejection would fight it.
 * - `water="in"` / `water="bank"` map to the spawner's `inWater`/`nearWater`
 *   anchoring modes.
 */
export function speciesSpawnSpec(
  plan: NatureRulesPlan,
  species: SpeciesRule,
  points: Array<[number, number]>
): SpawnGroupSpec {
  const attrs = species.spawnAttrs;
  const profileId = normalizeGroupProfileId(species.profile);
  const resolved = resolveGroupSpawnFields(attrs, profileId);

  const slopeMax = species.where.slope?.max;
  const maxSlopeDeg =
    slopeMax !== undefined && Number.isFinite(slopeMax)
      ? Math.max(0, Math.min(slopeMax, 90))
      : resolved.maxSlopeDeg;

  const inWater = species.where.waterMode === 'in';
  const nearWater = species.where.waterMode === 'bank';
  const hasWaterCond =
    inWater || nearWater || species.where.waterDist !== undefined;
  const hasRoadCond = species.where.roadDist !== undefined;

  const tplAttrs: Record<string, XMLValue> = {
    url: species.url,
    instanced: 'true',
  };
  if (species.lod1Url) tplAttrs['lod1-url'] = species.lod1Url;
  if (species.lod2Url) tplAttrs['lod2-url'] = species.lod2Url;

  return {
    mode: 'static',
    spawnGroupProfile: profileId,
    spawnCountMode: 'fixed',
    count: points.length,
    densityPerKm2: 0,
    countRangeMin: 0,
    countRangeMax: 0,
    seed: ((plan.seed >>> 0) ^ hashString(species.id)) >>> 0,
    regionMin: [...plan.regionMin] as [number, number, number],
    regionMax: [...plan.regionMax] as [number, number, number],
    alignToTerrain: resolved.alignToTerrain,
    baseYOffset: resolved.baseYOffset,
    groundAlign: resolved.groundAlign,
    randomYaw: resolved.randomYaw,
    scaleDistribution: resolved.scaleDistribution,
    scaleDiscreteValues: resolved.scaleDiscreteValues,
    scaleMin: resolved.scaleMin,
    scaleMax: resolved.scaleMax,
    scaleAxisMin: resolved.scaleAxisMin,
    scaleAxisMax: resolved.scaleAxisMax,
    yawDistribution: resolved.yawDistribution,
    yawDiscreteDeg: resolved.yawDiscreteDeg,
    surfaceEpsilon: resolved.surfaceEpsilon,
    surfaceEpsilonAuto: resolved.surfaceEpsilonAuto,
    maxSlopeDeg,
    maxSlopePlacementAttempts: resolved.maxSlopePlacementAttempts,
    pickStrategy: 'random',
    avoidWater: 'avoid-water' in attrs ? resolved.avoidWater : !hasWaterCond,
    avoidRoad: 'avoid-road' in attrs ? resolved.avoidRoad : !hasRoadCond,
    inWater,
    nearWater,
    avoidOverlaps: resolved.avoidOverlaps,
    footprintRadius: resolved.footprintRadius,
    maxDistance: resolved.maxDistance,
    instanced: true,
    clusterCount: 0,
    clusterRadius: 0,
    variation: resolveVariationSpec(attrs, profileId),
    templates: [
      { tagName: 'GLTFLoader', attributes: tplAttrs, role: 'visual' },
    ],
    points,
  };
}
