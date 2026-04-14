import type { Parser, XMLValue } from '../../core';
import { formatUnknownElement } from '../../core/recipes/diagnostics';
import { setSpawnGroupSpec } from './context';
import { prefetchGltfLocalYBounds } from '../gltf-xml/gltf-bounds-cache';
import {
  applyChildTemplateProfile,
  normalizeChildTemplateProfileId,
  normalizeGroupProfileId,
  resolveGroupSpawnFields,
} from './profiles';
import type {
  SpawnCountMode,
  SpawnGroupSpec,
  SpawnTemplateRole,
  SpawnTemplateSpec,
} from './types';

const VALID_ROLES = new Set<string>([
  'visual',
  'dynamic',
  'static',
  'kinematic',
  '',
]);

function parseRole(value: XMLValue | undefined): SpawnTemplateRole {
  if (value === undefined || value === null) return '';
  const s = String(value).trim().toLowerCase();
  if (s === '') return '';
  if (!VALID_ROLES.has(s)) {
    console.warn(
      `[spawn-group] role="${value}" desconhecido; use visual | dynamic | static | kinematic. Ignorado no spec.`
    );
    return '';
  }
  return s as SpawnTemplateRole;
}

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
    if ('x' in o) {
      return [o.x ?? 0, o.y ?? 0, o.z ?? 0];
    }
  }
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]), Number(value[1]), Number(value[2])];
  }
  if (typeof value === 'string') {
    const p = value
      .trim()
      .split(/\s+/)
      .map((x) => parseFloat(x));
    if (p.length >= 3) {
      return [p[0], p[1], p[2]];
    }
  }
  return fallback;
}

export const spawnGroupParser: Parser = ({ entity, element, state }) => {
  if (element.tagName.toLowerCase() !== 'spawngroup') return;

  const children = element.children.filter(
    (c) => c.tagName && c.tagName !== 'parsererror'
  );
  if (children.length === 0) {
    throw new Error(
      '[spawn-group] É necessário pelo menos um filho (template de recipe).\n' +
        '  Exemplo: <spawn-group count="4"><gltf-load url="..."></gltf-load></spawn-group>'
    );
  }

  const templates: SpawnTemplateSpec[] = [];
  for (const child of children) {
    if (!state.hasRecipe(child.tagName)) {
      const msg = formatUnknownElement(
        child.tagName,
        Array.from(state.getRecipeNames())
      );
      throw new Error(
        `[spawn-group] Filho inválido: ${msg}\n` +
          '  Use apenas tags com recipe registrado (ex.: gltf-load).'
      );
    }
    const attrs = { ...child.attributes };
    const childTplProfile = normalizeChildTemplateProfileId(attrs.profile);
    if ('profile' in attrs) {
      delete attrs.profile;
    }
    const roleRaw = attrs.role;
    if ('role' in attrs) {
      delete attrs.role;
    }
    applyChildTemplateProfile(child.tagName, attrs, childTplProfile);
    const tpl: SpawnTemplateSpec = {
      tagName: child.tagName,
      attributes: attrs,
      role: parseRole(roleRaw),
    };
    if (childTplProfile) {
      tpl.childProfile = childTplProfile;
    }
    if (child.tagName.toLowerCase() === 'gameobject') {
      const grand = child.children.filter(
        (c) => c.tagName && c.tagName !== 'parsererror'
      );
      if (grand.length > 0) {
        tpl.entityChildren = grand;
      }
    }
    templates.push(tpl);
  }

  const groupProfileId = normalizeGroupProfileId(
    element.attributes.profile as string | undefined
  );
  const resolvedSpawn = resolveGroupSpawnFields(
    element.attributes,
    groupProfileId
  );

  const densityRaw = element.attributes['density-per-km2'];
  const hasDensity =
    densityRaw !== undefined &&
    densityRaw !== null &&
    String(densityRaw).trim() !== '';
  const cminRaw = element.attributes['count-min'];
  const cmaxRaw = element.attributes['count-max'];
  const hasRange =
    cminRaw !== undefined &&
    cminRaw !== null &&
    String(cminRaw).trim() !== '' &&
    cmaxRaw !== undefined &&
    cmaxRaw !== null &&
    String(cmaxRaw).trim() !== '';

  let spawnCountMode: SpawnCountMode = 'fixed';
  let count = 0;
  let densityPerKm2 = 0;
  let countRangeMin = 0;
  let countRangeMax = 0;

  if (hasDensity) {
    spawnCountMode = 'density';
    densityPerKm2 = toNumber(densityRaw, 0);
    if (!Number.isFinite(densityPerKm2) || densityPerKm2 < 0) {
      throw new Error(
        '[spawn-group] density-per-km2 deve ser um número ≥ 0 (objetos por km² na área XZ).'
      );
    }
  } else if (hasRange) {
    spawnCountMode = 'random-range';
    countRangeMin = Math.floor(toNumber(cminRaw, 1));
    countRangeMax = Math.floor(toNumber(cmaxRaw, 1));
    if (countRangeMax < countRangeMin) {
      const t = countRangeMin;
      countRangeMin = countRangeMax;
      countRangeMax = t;
    }
    if (countRangeMin < 0) {
      throw new Error(
        '[spawn-group] count-min / count-max devem ser inteiros ≥ 0.'
      );
    }
  } else {
    spawnCountMode = 'fixed';
    count = Math.floor(toNumber(element.attributes.count, 0));
    if (count < 1) {
      throw new Error(
        '[spawn-group] Usa count="N" (N≥1), ou density-per-km2="…", ou count-min + count-max.\n' +
          '  Exemplo: <spawn-group count="12" …>'
      );
    }
  }

  const sdRaw = (element.attributes['scale-distribution'] as string | undefined)
    ?.trim()
    .toLowerCase();
  if (sdRaw === 'discrete' && resolvedSpawn.scaleDiscreteValues.length === 0) {
    throw new Error(
      '[spawn-group] scale-distribution="discrete" exige scale-discrete="1.5 2 3" (valores positivos).'
    );
  }
  const ydRaw = (element.attributes['yaw-distribution'] as string | undefined)
    ?.trim()
    .toLowerCase();
  const hasYawStep =
    element.attributes['yaw-step-deg'] !== undefined &&
    element.attributes['yaw-step-deg'] !== null &&
    String(element.attributes['yaw-step-deg']).trim() !== '';
  if (
    ydRaw === 'discrete' &&
    resolvedSpawn.randomYaw &&
    resolvedSpawn.yawDiscreteDeg.length === 0 &&
    !hasYawStep
  ) {
    throw new Error(
      '[spawn-group] yaw-distribution="discrete" exige yaw-discrete-deg="…" ou yaw-step-deg="45".'
    );
  }

  const pickRaw = (element.attributes['pick-strategy'] as string | undefined)
    ?.trim()
    .toLowerCase();
  let pickStrategy: SpawnGroupSpec['pickStrategy'] = 'random';
  if (pickRaw === 'round-robin' || pickRaw === 'round_robin') {
    pickStrategy = 'round-robin';
  } else if (pickRaw === 'random' || pickRaw === undefined) {
    pickStrategy = 'random';
  } else {
    throw new Error(
      `[spawn-group] pick-strategy inválido "${pickRaw}". Use "random" ou "round-robin".`
    );
  }

  const spec: SpawnGroupSpec = {
    spawnGroupProfile: groupProfileId,
    spawnCountMode,
    count: Math.floor(count),
    densityPerKm2,
    countRangeMin,
    countRangeMax,
    seed: Math.floor(toNumber(element.attributes.seed, 1)),
    regionMin: vec3FromAttr(element.attributes['region-min'], [0, 0, 0]),
    regionMax: vec3FromAttr(element.attributes['region-max'], [0, 0, 0]),
    alignToTerrain: resolvedSpawn.alignToTerrain,
    baseYOffset: resolvedSpawn.baseYOffset,
    groundAlign: resolvedSpawn.groundAlign,
    randomYaw: resolvedSpawn.randomYaw,
    scaleDistribution: resolvedSpawn.scaleDistribution,
    scaleDiscreteValues: resolvedSpawn.scaleDiscreteValues,
    scaleMin: resolvedSpawn.scaleMin,
    scaleMax: resolvedSpawn.scaleMax,
    yawDistribution: resolvedSpawn.yawDistribution,
    yawDiscreteDeg: resolvedSpawn.yawDiscreteDeg,
    surfaceEpsilon: resolvedSpawn.surfaceEpsilon,
    maxSlopeDeg: resolvedSpawn.maxSlopeDeg,
    maxSlopePlacementAttempts: resolvedSpawn.maxSlopePlacementAttempts,
    pickStrategy,
    avoidWater: resolvedSpawn.avoidWater,
    templates,
  };

  if (spec.scaleMax < spec.scaleMin) {
    const t = spec.scaleMin;
    spec.scaleMin = spec.scaleMax;
    spec.scaleMax = t;
  }

  setSpawnGroupSpec(state, entity, spec);

  for (const tpl of templates) {
    const u = tpl.attributes.url;
    if (typeof u === 'string' && u.trim()) {
      prefetchGltfLocalYBounds(u.trim());
    }
  }
};
