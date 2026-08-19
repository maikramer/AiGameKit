import { describe, expect, it } from 'bun:test';
import { speciesSpawnSpec } from '../../../src/plugins/nature/spec-from-rules';
import type {
  NatureRulesPlan,
  SpeciesRule,
} from '../../../src/plugins/nature/rules';

function plan(overrides?: Partial<NatureRulesPlan>): NatureRulesPlan {
  return {
    seed: 42,
    regionMin: [-100, 0, -100],
    regionMax: [100, 0, 100],
    spawnCountMode: 'fixed',
    count: 10,
    densityPerKm2: 0,
    minSpacing: 2.5,
    noiseScale: 90,
    species: [],
    groves: [],
    ...overrides,
  };
}

function species(overrides?: Partial<SpeciesRule>): SpeciesRule {
  return {
    id: 'oak',
    weight: 1,
    cap: 0,
    url: '/unit/oak_lod0.glb',
    where: {},
    profile: 'none',
    spawnAttrs: {},
    ...overrides,
  };
}

const POINTS: Array<[number, number]> = [
  [1, 2],
  [3, -4],
];

describe('speciesSpawnSpec', () => {
  it('carries the planned points and count', () => {
    const spec = speciesSpawnSpec(plan(), species(), POINTS);
    expect(spec.points).toEqual(POINTS);
    expect(spec.count).toBe(2);
    expect(spec.spawnCountMode).toBe('fixed');
    expect(spec.mode).toBe('static');
  });

  it('template is an instanced GLTFLoader with LOD urls', () => {
    const spec = speciesSpawnSpec(
      plan(),
      species({
        lod1Url: '/unit/oak_lod1.glb',
        lod2Url: '/unit/oak_lod2.glb',
      }),
      POINTS
    );
    expect(spec.templates).toHaveLength(1);
    const tpl = spec.templates[0]!;
    expect(tpl.tagName).toBe('GLTFLoader');
    expect(tpl.attributes.url).toBe('/unit/oak_lod0.glb');
    expect(tpl.attributes.instanced).toBe('true');
    expect(tpl.attributes['lod1-url']).toBe('/unit/oak_lod1.glb');
    expect(tpl.attributes['lod2-url']).toBe('/unit/oak_lod2.glb');
    expect(spec.instanced).toBe(true);
  });

  it('maxSlopeDeg derives from the Where slope upper bound', () => {
    const spec = speciesSpawnSpec(
      plan(),
      species({ where: { slope: { min: 5, max: 26 } } }),
      POINTS
    );
    expect(spec.maxSlopeDeg).toBe(26);
  });

  it('water mode maps to inWater/nearWater anchoring', () => {
    const floating = speciesSpawnSpec(
      plan(),
      species({ where: { waterMode: 'in' } }),
      POINTS
    );
    expect(floating.inWater).toBe(true);
    expect(floating.nearWater).toBe(false);
    expect(floating.avoidWater).toBe(false);

    const bank = speciesSpawnSpec(
      plan(),
      species({ where: { waterMode: 'bank' } }),
      POINTS
    );
    expect(bank.nearWater).toBe(true);
    expect(bank.inWater).toBe(false);
    expect(bank.avoidWater).toBe(false);
  });

  it('avoid-water/avoid-road default off when a distance condition exists', () => {
    const shore = speciesSpawnSpec(
      plan(),
      species({ where: { waterDist: { min: 0, max: 10 } } }),
      POINTS
    );
    expect(shore.avoidWater).toBe(false);
    expect(shore.avoidRoad).toBe(true);

    const roadside = speciesSpawnSpec(
      plan(),
      species({ where: { roadDist: { min: 0, max: 6 } } }),
      POINTS
    );
    expect(roadside.avoidRoad).toBe(false);
    expect(roadside.avoidWater).toBe(true);
  });

  it('explicit avoid-water/avoid-road attributes win over the defaults', () => {
    const sp = species({
      where: { waterDist: { min: 0, max: 10 } },
      spawnAttrs: { 'avoid-water': '1' },
    });
    const spec = speciesSpawnSpec(plan(), sp, POINTS);
    expect(spec.avoidWater).toBe(true);
  });

  it('species attributes resolve through group profiles', () => {
    const sp = species({
      profile: 'tree',
      spawnAttrs: {
        'scale-min': '0.8',
        'footprint-radius': '2.2',
        'max-distance': '170',
      },
    });
    const spec = speciesSpawnSpec(plan(), sp, POINTS);
    expect(spec.spawnGroupProfile).toBe('tree');
    expect(spec.alignToTerrain).toBe(true);
    expect(spec.groundAlign).toBe('aabb');
    expect(spec.randomYaw).toBe(true);
    expect(spec.scaleMin).toBe(0.8);
    expect(spec.footprintRadius).toBe(2.2);
    expect(spec.maxDistance).toBe(170);
  });

  it('seed is stable per species id and differs between species', () => {
    const a = speciesSpawnSpec(plan(), species({ id: 'oak' }), POINTS);
    const b = speciesSpawnSpec(plan(), species({ id: 'pine' }), POINTS);
    const a2 = speciesSpawnSpec(plan(), species({ id: 'oak' }), POINTS);
    expect(a.seed).toBe(a2.seed);
    expect(a.seed).not.toBe(b.seed);
  });
});
