import type { Parser, XMLValue } from '../../core';
import { setSpawnGroupSpec } from '../spawner/context';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import { resolveGroupSpawnFields } from '../spawner/profiles';
import type { SpawnGroupSpec, SpawnTemplateSpec } from '../spawner/types';
import { Vegetation } from './components';
import { parseVegetationMeshes, toBoolAttr } from './parse-meshes';
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

/**
 * `<Vegetation meshes="a.glb b.glb" density-per-km2="…" …>` — builds a
 * static SpawnGroupSpec (instanced GLTFLoader templates) so TerrainSpawnSystem
 * places the carpet. No child elements required.
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
  // Force foliage defaults unless the author overrides; dense carpet skips
  // overlap rejection by default (explicit avoid-overlaps="1" still works).
  if (attrs['avoid-overlaps'] === undefined) attrs['avoid-overlaps'] = '0';
  if (attrs['footprint-radius'] === undefined)
    attrs['footprint-radius'] = '0.2';
  if (attrs['max-distance'] === undefined) attrs['max-distance'] = '110';
  if (attrs['align-to-terrain'] === undefined) attrs['align-to-terrain'] = '1';
  if (attrs['avoid-water'] === undefined) attrs['avoid-water'] = '1';
  if (attrs['max-slope-deg'] === undefined) attrs['max-slope-deg'] = '35';
  // Kenney clumps ≈0.25 m tall → scale 1..3 ≈ world height 0.25–0.75 m.
  if (attrs['scale-min'] === undefined) attrs['scale-min'] = '1';
  if (attrs['scale-max'] === undefined) attrs['scale-max'] = '3';
  if (attrs['random-yaw'] === undefined) attrs['random-yaw'] = '1';
  if (attrs['ground-align'] === undefined) attrs['ground-align'] = 'aabb';

  const resolved = resolveGroupSpawnFields(attrs, 'foliage');

  const densityRaw = element.attributes['density-per-km2'];
  const hasDensity =
    densityRaw !== undefined &&
    densityRaw !== null &&
    String(densityRaw).trim() !== '';
  const countRaw = element.attributes.count;
  const hasCount =
    countRaw !== undefined &&
    countRaw !== null &&
    String(countRaw).trim() !== '';

  let spawnCountMode: SpawnGroupSpec['spawnCountMode'] = 'density';
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
    count = Math.floor(toNumber(countRaw, 0));
    if (count < 1) {
      throw new Error(
        '[Vegetation] count deve ser ≥ 1, ou usa density-per-km2.'
      );
    }
  } else {
    spawnCountMode = 'density';
    densityPerKm2 = 90000;
  }

  const templates: SpawnTemplateSpec[] = meshes.map((url) => ({
    tagName: 'GLTFLoader',
    attributes: {
      url,
      instanced: 'true',
    },
    role: 'visual',
  }));

  const spec: SpawnGroupSpec = {
    mode: 'static',
    spawnGroupProfile: 'foliage',
    spawnCountMode,
    count,
    densityPerKm2,
    countRangeMin: 0,
    countRangeMax: 0,
    seed: Math.floor(toNumber(element.attributes.seed, 1)),
    regionMin: vec3FromAttr(element.attributes['region-min'], [-40, 0, -40]),
    regionMax: vec3FromAttr(element.attributes['region-max'], [40, 0, 40]),
    alignToTerrain: resolved.alignToTerrain,
    baseYOffset: resolved.baseYOffset,
    groundAlign: resolved.groundAlign,
    randomYaw: resolved.randomYaw,
    scaleDistribution: resolved.scaleDistribution,
    scaleDiscreteValues: resolved.scaleDiscreteValues,
    scaleMin: resolved.scaleMin,
    scaleMax: resolved.scaleMax,
    yawDistribution: resolved.yawDistribution,
    yawDiscreteDeg: resolved.yawDiscreteDeg,
    surfaceEpsilon: resolved.surfaceEpsilon,
    surfaceEpsilonAuto: resolved.surfaceEpsilonAuto,
    maxSlopeDeg: resolved.maxSlopeDeg,
    maxSlopePlacementAttempts: resolved.maxSlopePlacementAttempts,
    pickStrategy: 'random',
    avoidWater: resolved.avoidWater,
    inWater: false,
    avoidOverlaps: resolved.avoidOverlaps,
    footprintRadius: resolved.footprintRadius,
    maxDistance: resolved.maxDistance,
    instanced: true,
    clusterCount: Math.max(
      0,
      Math.floor(toNumber(element.attributes['cluster-count'], 48))
    ),
    clusterRadius: Math.max(
      0.5,
      toNumber(element.attributes['cluster-radius'], 3.5)
    ),
    templates,
  };

  if (spec.scaleMax < spec.scaleMin) {
    const t = spec.scaleMin;
    spec.scaleMin = spec.scaleMax;
    spec.scaleMax = t;
  }

  setSpawnGroupSpec(state, entity, spec);

  const windOn = toBoolAttr(element.attributes.wind, true);
  Vegetation.wind[entity] = windOn ? 1 : 0;
  Vegetation.windRegistered[entity] = 0;
  if (windOn) {
    for (const url of meshes) registerVegetationWindUrl(state, url);
  }

  for (const url of meshes) prefetchGltfLocalYBounds(url);
};
