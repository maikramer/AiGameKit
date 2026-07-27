import type { SpawnGroupSpec, SpawnTemplateSpec } from '../spawner/types';
import type { VegetationLayerPlan, VegetationPatchPlan } from './plan';
import type { HubXZ } from './hubs';

export function templatesFromMeshes(meshes: string[]): SpawnTemplateSpec[] {
  return meshes.map((url) => ({
    tagName: 'GLTFLoader',
    attributes: {
      url,
      instanced: 'true',
    },
    role: 'visual',
  }));
}

/**
 * Build a foliage SpawnGroupSpec for one vegetation layer.
 * Pass shared `hubs` so flower/plant sample the same centres as grass.
 */
export function spawnSpecFromLayer(
  plan: VegetationPatchPlan,
  layer: VegetationLayerPlan,
  hubs: HubXZ[]
): SpawnGroupSpec {
  const clusterCenters =
    hubs.length > 0
      ? hubs.map((h) => [h[0], h[1]] as [number, number])
      : undefined;

  const roleSeed =
    layer.role === 'grass' ? 0 : layer.role === 'plant' ? 17 : 31;

  return {
    mode: 'static',
    spawnGroupProfile: 'foliage',
    spawnCountMode: plan.spawnCountMode,
    count: plan.spawnCountMode === 'fixed' ? Math.max(0, layer.count) : 0,
    densityPerKm2:
      plan.spawnCountMode === 'density' ? Math.max(0, layer.densityPerKm2) : 0,
    countRangeMin: 0,
    countRangeMax: 0,
    seed: (plan.seed + roleSeed) >>> 0,
    regionMin: [...plan.regionMin] as [number, number, number],
    regionMax: [...plan.regionMax] as [number, number, number],
    alignToTerrain: true,
    baseYOffset: 0,
    groundAlign: 'aabb',
    randomYaw: true,
    scaleDistribution: 'linear',
    scaleDiscreteValues: [],
    scaleMin: layer.scaleMin,
    scaleMax: layer.scaleMax,
    scaleAxisMin: plan.scaleAxisMin,
    scaleAxisMax: plan.scaleAxisMax,
    variation: plan.variation,
    yawDistribution: 'linear',
    yawDiscreteDeg: [],
    surfaceEpsilon: 0.75,
    surfaceEpsilonAuto: false,
    maxSlopeDeg: plan.maxSlopeDeg,
    maxSlopePlacementAttempts: 48,
    pickStrategy: 'random',
    avoidWater: plan.avoidWater,
    inWater: false,
    nearWater: false,
    avoidOverlaps: plan.avoidOverlaps,
    footprintRadius: plan.footprintRadius,
    maxDistance: plan.maxDistance,
    instanced: true,
    clusterCount: clusterCenters ? 0 : plan.clusterCount,
    clusterRadius: layer.clusterRadius,
    clusterCenters,
    templates: templatesFromMeshes(layer.meshes),
  };
}
