import type { VariationVisualSpec } from '../spawn-variation';
import type { VegetationRole } from './roles';
import { classifyVegetationRole, parseMeshRoleOverrides } from './roles';
import {
  applyPatchScaleOverride,
  resolveSizeTier,
  type VegetationSizeTier,
} from './size-tier';

export interface VegetationLayerPlan {
  role: VegetationRole;
  meshes: string[];
  /** Density relative to patch base (grass=1, plant/flower = ratios). */
  densityPerKm2: number;
  /** Fixed count when patch uses count mode (0 = use density). */
  count: number;
  scaleMin: number;
  scaleMax: number;
  /** Sample radius around shared hubs. */
  clusterRadius: number;
  /** Only grass layer generates hub count; others reuse hubs. */
  ownsHubs: boolean;
  tier: VegetationSizeTier;
}

export interface VegetationPatchPlan {
  smart: boolean;
  seed: number;
  regionMin: [number, number, number];
  regionMax: [number, number, number];
  clusterCount: number;
  /** Hub generation radius (grass). */
  clusterRadius: number;
  flowerNearRadius: number;
  wind: boolean;
  avoidWater: boolean;
  avoidRoad: boolean;
  avoidOverlaps: boolean;
  maxSlopeDeg: number;
  maxDistance: number;
  footprintRadius: number;
  spawnCountMode: 'density' | 'fixed';
  /** Base density before role ratios (density mode). */
  baseDensityPerKm2: number;
  /** Base count before role split (fixed mode). */
  baseCount: number;
  scaleAxisMin: number;
  scaleAxisMax: number;
  variation: VariationVisualSpec;
  layers: VegetationLayerPlan[];
  /** All mesh URLs (for wind / prefetch). */
  allMeshes: string[];
}

export interface BuildVegetationPlanInput {
  meshes: string[];
  smart: boolean;
  seed: number;
  regionMin: [number, number, number];
  regionMax: [number, number, number];
  clusterCount: number;
  clusterRadius: number;
  flowerNearRadius: number;
  flowerDensityRatio: number;
  plantDensityRatio: number;
  wind: boolean;
  avoidWater: boolean;
  avoidRoad: boolean;
  avoidOverlaps: boolean;
  maxSlopeDeg: number;
  maxDistance: number;
  footprintRadius: number;
  spawnCountMode: 'density' | 'fixed';
  densityPerKm2: number;
  count: number;
  /** null = use per-mesh tier defaults. */
  patchScaleMin: number | null;
  patchScaleMax: number | null;
  /** Per-axis proportion jitter (same contract as StaticSpawner). */
  scaleAxisMin: number;
  scaleAxisMax: number;
  /** Visual variation preset/spec (hue/brightness/contrast). */
  variation: VariationVisualSpec;
  meshRolesRaw?: string | null;
}

const ROLE_ORDER: VegetationRole[] = ['grass', 'plant', 'flower'];

function unionScale(
  meshes: string[],
  patchScaleMin: number | null,
  patchScaleMax: number | null
): { scaleMin: number; scaleMax: number; tier: VegetationSizeTier } {
  let scaleMin = Infinity;
  let scaleMax = -Infinity;
  let bestTier: VegetationSizeTier = 'medium';
  let bestH = -1;
  for (const url of meshes) {
    const tier = resolveSizeTier(url);
    const sc = applyPatchScaleOverride(tier, patchScaleMin, patchScaleMax);
    scaleMin = Math.min(scaleMin, sc.scaleMin);
    scaleMax = Math.max(scaleMax, sc.scaleMax);
    const h =
      tier.heightM ??
      (tier.tier === 'large' ? 0.4 : tier.tier === 'small' ? 0.15 : 0.28);
    if (h > bestH) {
      bestH = h;
      bestTier = tier.tier;
    }
  }
  if (!Number.isFinite(scaleMin)) {
    return { scaleMin: 1, scaleMax: 1.8, tier: 'medium' };
  }
  return { scaleMin, scaleMax, tier: bestTier };
}

/**
 * Build ordered vegetation layers from mesh URLs + patch knobs.
 * Flat (legacy) plan when smart=false or only one distinct role.
 */
export function buildVegetationPlan(
  input: BuildVegetationPlanInput
): VegetationPatchPlan {
  const overrides = parseMeshRoleOverrides(input.meshRolesRaw);
  const byRole = new Map<VegetationRole, string[]>();
  for (const url of input.meshes) {
    const role = classifyVegetationRole(url, overrides);
    const list = byRole.get(role) ?? [];
    list.push(url);
    byRole.set(role, list);
  }

  const distinctRoles = ROLE_ORDER.filter(
    (r) => (byRole.get(r)?.length ?? 0) > 0
  );
  const useSmart = input.smart && distinctRoles.length >= 2;

  const layers: VegetationLayerPlan[] = [];

  if (!useSmart) {
    const sc = unionScale(
      input.meshes,
      input.patchScaleMin,
      input.patchScaleMax
    );
    layers.push({
      role: distinctRoles[0] ?? 'grass',
      meshes: [...input.meshes],
      densityPerKm2: input.densityPerKm2,
      count: input.count,
      scaleMin: sc.scaleMin,
      scaleMax: sc.scaleMax,
      clusterRadius: input.clusterRadius,
      ownsHubs: true,
      tier: sc.tier,
    });
  } else {
    const grassMeshes = byRole.get('grass') ?? [];
    const plantMeshes = byRole.get('plant') ?? [];
    const flowerMeshes = byRole.get('flower') ?? [];

    if (grassMeshes.length > 0) {
      const sc = unionScale(
        grassMeshes,
        input.patchScaleMin,
        input.patchScaleMax
      );
      layers.push({
        role: 'grass',
        meshes: grassMeshes,
        densityPerKm2: input.densityPerKm2,
        count: input.count,
        scaleMin: sc.scaleMin,
        scaleMax: sc.scaleMax,
        clusterRadius: input.clusterRadius,
        ownsHubs: true,
        tier: sc.tier,
      });
    }

    if (plantMeshes.length > 0) {
      const sc = unionScale(
        plantMeshes,
        input.patchScaleMin,
        input.patchScaleMax
      );
      const ratio = Math.max(0, input.plantDensityRatio);
      layers.push({
        role: 'plant',
        meshes: plantMeshes,
        densityPerKm2: input.densityPerKm2 * ratio,
        count: Math.max(0, Math.floor(input.count * ratio)),
        scaleMin: sc.scaleMin,
        scaleMax: sc.scaleMax,
        clusterRadius: Math.max(
          0.8,
          Math.min(input.clusterRadius, input.flowerNearRadius * 1.4)
        ),
        ownsHubs: grassMeshes.length === 0,
        tier: sc.tier,
      });
    }

    if (flowerMeshes.length > 0) {
      const sc = unionScale(
        flowerMeshes,
        input.patchScaleMin,
        input.patchScaleMax
      );
      const ratio = Math.max(0, input.flowerDensityRatio);
      layers.push({
        role: 'flower',
        meshes: flowerMeshes,
        densityPerKm2: input.densityPerKm2 * ratio,
        count: Math.max(0, Math.floor(input.count * ratio)),
        scaleMin: sc.scaleMin,
        scaleMax: sc.scaleMax,
        clusterRadius: Math.max(0.5, input.flowerNearRadius),
        ownsHubs: grassMeshes.length === 0 && plantMeshes.length === 0,
        tier: sc.tier,
      });
    }

    // Ensure at least one layer owns hubs.
    if (layers.length > 0 && !layers.some((l) => l.ownsHubs)) {
      layers[0]!.ownsHubs = true;
    }
  }

  const axisLo = Math.min(input.scaleAxisMin, input.scaleAxisMax);
  const axisHi = Math.max(input.scaleAxisMin, input.scaleAxisMax);

  return {
    smart: useSmart,
    seed: input.seed,
    regionMin: input.regionMin,
    regionMax: input.regionMax,
    clusterCount: input.clusterCount,
    clusterRadius: input.clusterRadius,
    flowerNearRadius: input.flowerNearRadius,
    wind: input.wind,
    avoidWater: input.avoidWater,
    avoidRoad: input.avoidRoad,
    avoidOverlaps: input.avoidOverlaps,
    maxSlopeDeg: input.maxSlopeDeg,
    maxDistance: input.maxDistance,
    footprintRadius: input.footprintRadius,
    spawnCountMode: input.spawnCountMode,
    baseDensityPerKm2: input.densityPerKm2,
    baseCount: input.count,
    scaleAxisMin: axisLo,
    scaleAxisMax: axisHi,
    variation: input.variation,
    layers,
    allMeshes: [...input.meshes],
  };
}
