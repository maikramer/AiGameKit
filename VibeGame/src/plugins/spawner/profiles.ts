import type { XMLValue } from '../../core';
import type { ScaleDistributionMode, YawDistributionMode } from './types';

/** Perfis de `<spawn-group profile="...">`. `none` = legado (mesmos fallbacks do parser antes dos perfis). */
export type SpawnGroupProfileId =
  | 'none'
  | 'tree'
  | 'foliage'
  | 'physics-box'
  | 'gltf-crate'
  /** Defaults for `place="…"` on `<entity>` (deterministic terrain placement). */
  | 'place';

/** Defaults de spawn resolvidos antes do `TerrainSpawnSystem`. */
/** `aabb`: desloca a origem com `-minY×escala` ao longo da normal (ou +Y se sem alinhar ao terreno), a partir do AABB local do GLB. */
export type GroundAlignMode = 'none' | 'aabb';

export interface GroupSpawnDefaults {
  alignToTerrain: boolean;
  baseYOffset: number;
  /** Ajuste fino em Y mundo depois do alinhamento por bounds (ou sozinho se `none`). */
  groundAlign: GroundAlignMode;
  randomYaw: boolean;
  scaleMin: number;
  scaleMax: number;
  surfaceEpsilon: number;
  /**
   * Inclinação máxima aceite (graus): ângulo entre a normal do terreno e +Y.
   * Acima disto, o spawn re-amostra uma posição aleatória na região (até `maxSlopePlacementAttempts`).
   */
  maxSlopeDeg: number;
  /** Tentativas de posição aleatória por instância; se nenhuma cumprir o declive (com max-slope-deg menor que 90°), a instância é omitida. */
  maxSlopePlacementAttempts: number;
  /** Re-amostra posições que cairiam sob planos de água (lagos). */
  avoidWater: boolean;
  scaleDistribution: ScaleDistributionMode;
  yawDistribution: YawDistributionMode;
  /** Valores positivos para `scale-distribution=discrete` (XML `scale-discrete`). */
  scaleDiscreteValues: number[];
  /** Graus para yaw discreto (XML `yaw-discrete-deg` / `yaw-step-deg`). */
  yawDiscreteDeg: number[];
}

const LEGACY: GroupSpawnDefaults = {
  alignToTerrain: false,
  baseYOffset: 0,
  groundAlign: 'none',
  randomYaw: false,
  scaleMin: 1,
  scaleMax: 1,
  surfaceEpsilon: 0.75,
  maxSlopeDeg: 45,
  maxSlopePlacementAttempts: 32,
  avoidWater: false,
  scaleDistribution: 'linear',
  yawDistribution: 'linear',
  scaleDiscreteValues: [],
  yawDiscreteDeg: [],
};

const GROUP_PROFILES: Record<SpawnGroupProfileId, GroupSpawnDefaults> = {
  none: LEGACY,
  tree: {
    alignToTerrain: true,
    groundAlign: 'aabb',
    baseYOffset: 0.02,
    randomYaw: true,
    scaleMin: 1.6,
    scaleMax: 2.2,
    surfaceEpsilon: 0.75,
    maxSlopeDeg: 45,
    maxSlopePlacementAttempts: 48,
    avoidWater: true,
    scaleDistribution: 'linear',
    yawDistribution: 'linear',
    scaleDiscreteValues: [],
    yawDiscreteDeg: [],
  },
  foliage: {
    alignToTerrain: true,
    groundAlign: 'aabb',
    baseYOffset: 0.02,
    randomYaw: true,
    scaleMin: 0.9,
    scaleMax: 1.3,
    surfaceEpsilon: 0.75,
    maxSlopeDeg: 45,
    maxSlopePlacementAttempts: 48,
    avoidWater: true,
    scaleDistribution: 'linear',
    yawDistribution: 'linear',
    scaleDiscreteValues: [],
    yawDiscreteDeg: [],
  },
  'physics-box': {
    alignToTerrain: false,
    groundAlign: 'none',
    baseYOffset: 0.425,
    randomYaw: true,
    scaleMin: 1,
    scaleMax: 1,
    surfaceEpsilon: 0.75,
    maxSlopeDeg: 45,
    maxSlopePlacementAttempts: 32,
    avoidWater: false,
    scaleDistribution: 'linear',
    yawDistribution: 'linear',
    scaleDiscreteValues: [],
    yawDiscreteDeg: [],
  },
  'gltf-crate': {
    alignToTerrain: false,
    groundAlign: 'none',
    baseYOffset: 0.35,
    randomYaw: true,
    scaleMin: 0.85,
    scaleMax: 1.1,
    surfaceEpsilon: 0.75,
    maxSlopeDeg: 45,
    maxSlopePlacementAttempts: 32,
    avoidWater: false,
    scaleDistribution: 'linear',
    yawDistribution: 'linear',
    scaleDiscreteValues: [],
    yawDiscreteDeg: [],
  },
  place: {
    alignToTerrain: true,
    groundAlign: 'aabb',
    baseYOffset: 0,
    randomYaw: false,
    scaleMin: 1,
    scaleMax: 1,
    surfaceEpsilon: 0.75,
    maxSlopeDeg: 90,
    maxSlopePlacementAttempts: 1,
    avoidWater: false,
    scaleDistribution: 'linear',
    yawDistribution: 'linear',
    scaleDiscreteValues: [],
    yawDiscreteDeg: [],
  },
};

const KNOWN_GROUP_PROFILES = new Set<string>(Object.keys(GROUP_PROFILES));

/** Perfil opcional em filhos (`profile="physics-crate"` / `gltf-crate`). */
export type ChildTemplateProfileId = '' | 'physics-crate' | 'gltf-crate';

const KNOWN_CHILD_PROFILES = new Set<string>([
  '',
  'physics-crate',
  'gltf-crate',
]);

export function normalizeGroupProfileId(
  raw: string | undefined
): SpawnGroupProfileId {
  const s = (raw ?? '').trim().toLowerCase();
  if (s === '' || s === 'none') return 'none';
  if (s in GROUP_PROFILES) return s as SpawnGroupProfileId;
  console.warn(
    `[spawn-group] profile="${raw}" desconhecido; use none | tree | foliage | physics-box | gltf-crate | place. Usando "none".`
  );
  return 'none';
}

export function getGroupSpawnDefaults(
  id: SpawnGroupProfileId
): GroupSpawnDefaults {
  return GROUP_PROFILES[id] ?? LEGACY;
}

export function normalizeChildTemplateProfileId(
  raw: XMLValue | undefined
): ChildTemplateProfileId {
  if (raw === undefined || raw === null) return '';
  const s = String(raw).trim().toLowerCase();
  if (s === '') return '';
  if (KNOWN_CHILD_PROFILES.has(s)) return s as ChildTemplateProfileId;
  console.warn(
    `[spawn-group] profile no filho="${raw}" desconhecido; use physics-crate | gltf-crate. Ignorado.`
  );
  return '';
}

/**
 * Preenche apenas chaves ausentes em `attrs` (template de recipe).
 */
export function applyChildTemplateProfile(
  tagName: string,
  attrs: Record<string, XMLValue>,
  childProfile: ChildTemplateProfileId
): void {
  if (!childProfile) return;

  if (
    childProfile === 'physics-crate' &&
    tagName.toLowerCase() === 'dynamic-part'
  ) {
    if (!('shape' in attrs)) attrs.shape = 'box';
    if (!('size' in attrs)) attrs.size = '0.85 0.85 0.85';
    if (!('color' in attrs)) attrs.color = '#8b6914';
    if (!('mass' in attrs)) attrs.mass = 1.2;
    if (!('restitution' in attrs)) attrs.restitution = 0.15;
    return;
  }

  if (
    childProfile === 'gltf-crate' &&
    tagName.toLowerCase() === 'gltfdynamic'
  ) {
    if (!('mass' in attrs)) attrs.mass = 1.5;
    if (!('friction' in attrs)) attrs.friction = 0.55;
    if (!('collider-margin' in attrs)) attrs['collider-margin'] = 0.02;
    if (!('collider-shape' in attrs)) attrs['collider-shape'] = 'box';
  }
}

export function isKnownGroupProfileForTests(id: string): boolean {
  return KNOWN_GROUP_PROFILES.has(id);
}

function rawToBool01(value: XMLValue): number {
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') return value !== 0 ? 1 : 0;
  if (typeof value === 'string') {
    const s = value.trim().toLowerCase();
    if (s === '1' || s === 'true' || s === 'yes') return 1;
    if (s === '0' || s === 'false' || s === 'no') return 0;
    const n = parseFloat(s);
    if (!Number.isNaN(n)) return n !== 0 ? 1 : 0;
  }
  return 0;
}

/** Atributo ausente → `profileVal`; presente → parse como número (como o parser). */
export function optNumber(
  attr: XMLValue | undefined,
  profileVal: number
): number {
  if (attr === undefined || attr === null) return profileVal;
  if (typeof attr === 'number') return attr;
  if (typeof attr === 'boolean') return attr ? 1 : 0;
  if (typeof attr === 'string') {
    const n = parseFloat(attr);
    return Number.isNaN(n) ? profileVal : n;
  }
  return profileVal;
}

/** Atributo ausente → `profileVal`; presente → parse como 0/1. */
export function optBool(
  attr: XMLValue | undefined,
  profileVal: boolean
): boolean {
  if (attr === undefined || attr === null) return profileVal;
  return rawToBool01(attr) === 1;
}

/**
 * Resolve campos de spawn do grupo a partir dos atributos XML + perfil.
 * Usado pelo parser e testável sem ECS.
 */
function optGroundAlign(
  attr: XMLValue | undefined,
  profileVal: GroundAlignMode
): GroundAlignMode {
  if (attr === undefined || attr === null) return profileVal;
  const s = String(attr).trim().toLowerCase();
  if (s === 'aabb' || s === 'bounds' || s === 'auto') return 'aabb';
  if (s === 'none' || s === '0' || s === 'false' || s === 'no') return 'none';
  return profileVal;
}

function optScaleDistribution(
  attr: XMLValue | undefined,
  profileVal: ScaleDistributionMode
): ScaleDistributionMode {
  if (attr === undefined || attr === null) return profileVal;
  const s = String(attr).trim().toLowerCase();
  if (s === 'linear' || s === 'continuous') return 'linear';
  if (s === 'discrete') return 'discrete';
  return profileVal;
}

function optYawDistribution(
  attr: XMLValue | undefined,
  profileVal: YawDistributionMode
): YawDistributionMode {
  if (attr === undefined || attr === null) return profileVal;
  const s = String(attr).trim().toLowerCase();
  if (s === 'linear' || s === 'continuous') return 'linear';
  if (s === 'discrete') return 'discrete';
  return profileVal;
}

/** Números separados por espaço (XML `scale-discrete`, `yaw-discrete-deg`). */
export function parseSpaceSeparatedNumbers(raw: XMLValue | undefined): number[] {
  if (raw === undefined || raw === null) return [];
  const s = String(raw).trim();
  if (!s) return [];
  return s
    .split(/\s+/)
    .map((x) => parseFloat(x))
    .filter((n) => !Number.isNaN(n));
}

/** Ângulos 0, step, 2·step, … &lt; 360° para yaw discreto (atributo `yaw-step-deg`). */
export function yawAnglesFromStepDeg(stepDeg: number): number[] {
  if (!(stepDeg > 0) || stepDeg > 360) return [];
  const out: number[] = [];
  for (let a = 0; a < 360 - 1e-9; a += stepDeg) {
    out.push(Math.round(a * 1000) / 1000);
  }
  return out;
}

export function resolveGroupSpawnFields(
  attrs: Record<string, XMLValue>,
  profileId: SpawnGroupProfileId
): GroupSpawnDefaults {
  const p = getGroupSpawnDefaults(profileId);

  let scaleDiscrete = parseSpaceSeparatedNumbers(attrs['scale-discrete']).filter(
    (x) => x > 0
  );
  let yawDeg = parseSpaceSeparatedNumbers(attrs['yaw-discrete-deg']);
  const stepYaw = optNumber(attrs['yaw-step-deg'], NaN);
  if (yawDeg.length === 0 && Number.isFinite(stepYaw) && stepYaw > 0) {
    yawDeg = yawAnglesFromStepDeg(stepYaw);
  }
  yawDeg = yawDeg.map((d) => {
    const m = d % 360;
    return m < 0 ? m + 360 : m;
  });

  let scaleDistribution = optScaleDistribution(
    attrs['scale-distribution'],
    p.scaleDistribution
  );
  let yawDistribution = optYawDistribution(
    attrs['yaw-distribution'],
    p.yawDistribution
  );
  if (scaleDiscrete.length > 0) scaleDistribution = 'discrete';
  if (yawDeg.length > 0) yawDistribution = 'discrete';

  return {
    alignToTerrain: optBool(attrs['align-to-terrain'], p.alignToTerrain),
    baseYOffset: optNumber(attrs['base-y-offset'], p.baseYOffset),
    groundAlign: optGroundAlign(attrs['ground-align'], p.groundAlign),
    randomYaw: optBool(attrs['random-yaw'], p.randomYaw),
    scaleMin: optNumber(attrs['scale-min'], p.scaleMin),
    scaleMax: optNumber(attrs['scale-max'], p.scaleMax),
    surfaceEpsilon: optNumber(attrs['surface-epsilon'], p.surfaceEpsilon),
    maxSlopeDeg: optNumber(attrs['max-slope-deg'], p.maxSlopeDeg),
    maxSlopePlacementAttempts: Math.max(
      1,
      Math.floor(
        optNumber(attrs['max-slope-attempts'], p.maxSlopePlacementAttempts)
      )
    ),
    avoidWater: optBool(attrs['avoid-water'], p.avoidWater),
    scaleDistribution,
    yawDistribution,
    scaleDiscreteValues:
      scaleDiscrete.length > 0 ? scaleDiscrete : [...p.scaleDiscreteValues],
    yawDiscreteDeg: yawDeg.length > 0 ? yawDeg : [...p.yawDiscreteDeg],
  };
}
