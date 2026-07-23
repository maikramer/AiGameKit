import type { Parser, XMLValue } from '../../core';
import { setSpawnGroupSpec } from '../spawner/context';
import { SpawnerPending } from '../spawner/components';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import { resolveVariationSpec } from '../spawn-variation';
import { resolveGroupSpawnFields } from '../spawner/profiles';
import type { SpawnGroupSpec } from '../spawner/types';
import { Vegetation } from './components';
import { parseVegetationMeshes, toBoolAttr } from './parse-meshes';
import { buildVegetationPlan } from './plan';
import { setVegetationPatch } from './patch-context';
import { spawnSpecFromLayer } from './spec-from-plan';
import { registerVegetationWindUrl } from './wind';

function toNumber(value: XMLValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'string') {
    const n = parseFloat(value);
    return Number.isNaN(n) ? fallback : n;
  }
  return fallback;
}

function vec3FromAttr(
  value: XMLValue | undefined,
  fallback: [number, number, number]
): [number, number, number] {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const o = value as Record<string, number>;
    if ('x' in o) return [o.x ?? 0, o.y ?? 0, o.z ?? 0];
  }
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]), Number(value[1]), Number(value[2])];
  }
  if (typeof value === 'string') {
    const p = value
      .trim()
      .split(/\s+/)
      .map((x) => parseFloat(x));
    if (p.length >= 3) return [p[0]!, p[1]!, p[2]!];
  }
  return fallback;
}

function hasAttr(attrs: Record<string, XMLValue>, key: string): boolean {
  const v = attrs[key];
  return v !== undefined && v !== null && String(v).trim() !== '';
}

/**
 * `<Vegetation meshes="a.glb b.glb" density-per-km2="…" …>` — builds a
 * static SpawnGroupSpec (legacy) or a smart multi-layer patch (grass hubs →
 * flowers nearby) materialized by VegetationPlannerSystem.
 */
export const vegetationParser: Parser = ({ entity, element, state }) => {
  const meshes = parseVegetationMeshes(element.attributes.meshes);
  if (meshes.length === 0) {
    throw new Error(
      '[Vegetation] meshes="…" é obrigatório (lista de URLs GLB separadas por espaço).\n' +
        '  Exemplo: <Vegetation meshes="/assets/meshes/vegetation/grass.glb" density-per-km2="12000" region-min="-40 0 -40" region-max="40 0 40"></Vegetation>'
    );
  }

  const attrs = { ...element.attributes };
  if (attrs['avoid-overlaps'] === undefined) attrs['avoid-overlaps'] = '0';
  if (attrs['footprint-radius'] === undefined)
    attrs['footprint-radius'] = '0.2';
  if (attrs['max-distance'] === undefined) attrs['max-distance'] = '110';
  if (attrs['align-to-terrain'] === undefined) attrs['align-to-terrain'] = '1';
  if (attrs['avoid-water'] === undefined) attrs['avoid-water'] = '1';
  if (attrs['max-slope-deg'] === undefined) attrs['max-slope-deg'] = '35';
  if (attrs['random-yaw'] === undefined) attrs['random-yaw'] = '1';
  if (attrs['ground-align'] === undefined) attrs['ground-align'] = 'aabb';

  const authorScaleMin = hasAttr(element.attributes, 'scale-min');
  const authorScaleMax = hasAttr(element.attributes, 'scale-max');
  // Legacy defaults only when author omits scale (smart tiers supply their own).
  if (!authorScaleMin) attrs['scale-min'] = '1';
  if (!authorScaleMax) attrs['scale-max'] = '3';

  const resolved = resolveGroupSpawnFields(attrs, 'foliage');

  const densityRaw = element.attributes['density-per-km2'];
  const hasDensity = hasAttr(element.attributes, 'density-per-km2');
  const hasCount = hasAttr(element.attributes, 'count');

  let spawnCountMode: SpawnGroupSpec['spawnCountMode'];
  let count = 0;
  let densityPerKm2 = 90000;
  if (hasDensity) {
    spawnCountMode = 'density';
    densityPerKm2 = toNumber(densityRaw, 90000);
    if (!Number.isFinite(densityPerKm2) || densityPerKm2 < 0) {
      throw new Error(
        '[Vegetation] density-per-km2 deve ser um número ≥ 0 (objetos por km²).'
      );
    }
  } else if (hasCount) {
    spawnCountMode = 'fixed';
    count = Math.floor(toNumber(element.attributes.count, 0));
    if (count < 1) {
      throw new Error(
        '[Vegetation] count deve ser ≥ 1, ou usa density-per-km2.'
      );
    }
  } else {
    spawnCountMode = 'density';
    densityPerKm2 = 90000;
  }

  const smart = toBoolAttr(element.attributes.smart, true);
  const plan = buildVegetationPlan({
    meshes,
    smart,
    seed: Math.floor(toNumber(element.attributes.seed, 1)),
    regionMin: vec3FromAttr(element.attributes['region-min'], [-40, 0, -40]),
    regionMax: vec3FromAttr(element.attributes['region-max'], [40, 0, 40]),
    clusterCount: Math.max(
      0,
      Math.floor(toNumber(element.attributes['cluster-count'], 48))
    ),
    clusterRadius: Math.max(
      0.5,
      toNumber(element.attributes['cluster-radius'], 3.5)
    ),
    flowerNearRadius: Math.max(
      0.5,
      toNumber(element.attributes['flower-near-radius'], 2.2)
    ),
    flowerDensityRatio: Math.max(
      0,
      toNumber(element.attributes['flower-density-ratio'], 0.15)
    ),
    plantDensityRatio: Math.max(
      0,
      toNumber(element.attributes['plant-density-ratio'], 0.25)
    ),
    wind: toBoolAttr(element.attributes.wind, true),
    avoidWater: resolved.avoidWater,
    avoidOverlaps: resolved.avoidOverlaps,
    maxSlopeDeg: resolved.maxSlopeDeg,
    maxDistance: resolved.maxDistance,
    footprintRadius: resolved.footprintRadius,
    spawnCountMode: spawnCountMode === 'fixed' ? 'fixed' : 'density',
    densityPerKm2,
    count,
    patchScaleMin: authorScaleMin ? resolved.scaleMin : null,
    patchScaleMax: authorScaleMax ? resolved.scaleMax : null,
    scaleAxisMin: resolved.scaleAxisMin,
    scaleAxisMax: resolved.scaleAxisMax,
    variation: resolveVariationSpec(element.attributes, 'foliage'),
    meshRolesRaw:
      element.attributes['mesh-roles'] !== undefined
        ? String(element.attributes['mesh-roles'])
        : null,
  });

  const windOn = plan.wind;
  Vegetation.wind[entity] = windOn ? 1 : 0;
  if (windOn) {
    for (const url of plan.allMeshes) registerVegetationWindUrl(state, url);
  }
  Vegetation.windRegistered[entity] = windOn ? 1 : 0;

  for (const url of plan.allMeshes) prefetchGltfLocalYBounds(url);

  if (plan.smart) {
    // Parent does not spawn; VegetationPlannerSystem creates layer children.
    SpawnerPending.spawned[entity] = 1;
    setVegetationPatch(state, entity, {
      plan,
      layerEntities: [],
      hubsReady: false,
    });
    return;
  }

  // Legacy / single-role: one spec on this entity.
  const layer = plan.layers[0]!;
  const spec = spawnSpecFromLayer(plan, layer, []);
  // Without precomputed hubs, use clusterCount generation.
  spec.clusterCount = plan.clusterCount;
  spec.clusterCenters = undefined;
  // Honor resolve scale when author set scale attrs (already in layer via plan).
  if (authorScaleMin || authorScaleMax) {
    spec.scaleMin = layer.scaleMin;
    spec.scaleMax = layer.scaleMax;
  }
  if (spec.scaleMax < spec.scaleMin) {
    const t = spec.scaleMin;
    spec.scaleMin = spec.scaleMax;
    spec.scaleMax = t;
  }
  setSpawnGroupSpec(state, entity, spec);
  setVegetationPatch(state, entity, {
    plan,
    layerEntities: [],
    hubsReady: true,
  });
};
