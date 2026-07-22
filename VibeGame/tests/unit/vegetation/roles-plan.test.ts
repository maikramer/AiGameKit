import { describe, expect, it } from 'bun:test';
import {
  classifyVegetationRole,
  parseMeshRoleOverrides,
} from '../../../src/plugins/vegetation/roles';
import {
  sizeTierFromHeight,
  sizeTierFromFilename,
} from '../../../src/plugins/vegetation/size-tier';
import { buildVegetationPlan } from '../../../src/plugins/vegetation/plan';

describe('classifyVegetationRole', () => {
  it('detects grass / flower / plant by filename', () => {
    expect(classifyVegetationRole('/assets/meshes/vegetation/grass.glb')).toBe(
      'grass'
    );
    expect(
      classifyVegetationRole('/assets/meshes/vegetation/grass_large.glb')
    ).toBe('grass');
    expect(
      classifyVegetationRole('/assets/meshes/vegetation/flower_yellowA.glb')
    ).toBe('flower');
    expect(
      classifyVegetationRole('/assets/meshes/vegetation/plant_flatShort.glb')
    ).toBe('plant');
  });

  it('honours mesh-roles overrides', () => {
    const o = parseMeshRoleOverrides('/a.glb:flower,/assets/x.glb:grass');
    expect(classifyVegetationRole('/a.glb', o)).toBe('flower');
    expect(classifyVegetationRole('/assets/x.glb', o)).toBe('grass');
  });

  it('unknown → plant', () => {
    expect(classifyVegetationRole('/props/bush_clump.glb')).toBe('plant');
  });
});

describe('sizeTierFromHeight / filename', () => {
  it('maps height bands', () => {
    expect(sizeTierFromHeight(0.15)).toBe('small');
    expect(sizeTierFromHeight(0.28)).toBe('medium');
    expect(sizeTierFromHeight(0.4)).toBe('large');
  });

  it('filename hints', () => {
    expect(sizeTierFromFilename('grass_large.glb')).toBe('large');
    expect(sizeTierFromFilename('plant_flatShort.glb')).toBe('small');
    expect(sizeTierFromFilename('grass.glb')).toBeNull();
  });
});

describe('buildVegetationPlan', () => {
  const base = {
    seed: 41,
    regionMin: [-10, 0, -10] as [number, number, number],
    regionMax: [10, 0, 10] as [number, number, number],
    clusterCount: 20,
    clusterRadius: 3.5,
    flowerNearRadius: 2.2,
    flowerDensityRatio: 0.15,
    plantDensityRatio: 0.25,
    wind: true,
    avoidWater: true,
    avoidOverlaps: false,
    maxSlopeDeg: 35,
    maxDistance: 110,
    footprintRadius: 0.2,
    spawnCountMode: 'density' as const,
    densityPerKm2: 40000,
    count: 0,
    patchScaleMin: null,
    patchScaleMax: null,
    scaleAxisMin: 0.9,
    scaleAxisMax: 1.1,
    variation: {
      preset: 'foliage' as const,
      hueJitterDeg: 8,
      saturationMin: 0.9,
      saturationMax: 1.12,
      brightnessMin: 0.88,
      brightnessMax: 1.14,
      contrastMin: 0.92,
      contrastMax: 1.1,
      spatial: 0.45,
    },
  };

  it('smart=false → single flat layer', () => {
    const plan = buildVegetationPlan({
      ...base,
      smart: false,
      meshes: ['/v/grass.glb', '/v/flower_redA.glb'],
    });
    expect(plan.smart).toBe(false);
    expect(plan.layers).toHaveLength(1);
    expect(plan.layers[0]!.meshes).toHaveLength(2);
  });

  it('smart with grass+flower+plant → 3 layers, flower near radius', () => {
    const plan = buildVegetationPlan({
      ...base,
      smart: true,
      meshes: [
        '/v/grass.glb',
        '/v/grass_large.glb',
        '/v/plant_flatShort.glb',
        '/v/flower_yellowA.glb',
      ],
    });
    expect(plan.smart).toBe(true);
    expect(plan.layers.map((l) => l.role)).toEqual([
      'grass',
      'plant',
      'flower',
    ]);
    const grass = plan.layers.find((l) => l.role === 'grass')!;
    const plant = plan.layers.find((l) => l.role === 'plant')!;
    const flower = plan.layers.find((l) => l.role === 'flower')!;
    expect(grass.ownsHubs).toBe(true);
    expect(grass.densityPerKm2).toBe(40000);
    expect(plant.densityPerKm2).toBeCloseTo(10000);
    expect(flower.densityPerKm2).toBeCloseTo(6000);
    expect(flower.clusterRadius).toBe(2.2);
    expect(flower.ownsHubs).toBe(false);
    expect(grass.meshes).toHaveLength(2);
  });

  it('single role stays flat even when smart=true', () => {
    const plan = buildVegetationPlan({
      ...base,
      smart: true,
      meshes: ['/v/grass.glb', '/v/grass_large.glb'],
    });
    expect(plan.smart).toBe(false);
    expect(plan.layers).toHaveLength(1);
  });
});
