import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { planNatureSpawns } from '../../../src/plugins/nature/planner';
import type {
  NatureRulesPlan,
  SpeciesRule,
} from '../../../src/plugins/nature/rules';
import { registerSpawnFootprint } from '../../../src/plugins/spawner/occupancy';
import { Terrain } from '../../../src/plugins/terrain/components';
import { registerGroundBrush } from '../../../src/plugins/terrain/brush-registry';
import { getTerrainContext } from '../../../src/plugins/terrain/utils';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { registerWaterBody } from '../../../src/plugins/water/registry';
import { sampleTerrainSurface } from '../../../src/plugins/spawner/surface';

/**
 * Planner contract over a synthetic stepped terrain: south half (z < 0) at
 * altitude 5, north half at 20. Every assertion runs on the planner's point
 * buckets — the TerrainSpawnSystem placement path is covered by
 * tests/unit/spawner/points-mode.test.ts.
 */

const WORLD = 200;
const SOUTH_H = 5;
const NORTH_H = 20;

function buildState(): State {
  const state = new State();
  state.registerComponent('terrain', Terrain);

  const terrain = state.createEntity();
  state.addComponent(terrain, Terrain);
  Terrain.resolution[terrain] = 64;
  Terrain.levels[terrain] = 4;

  const width = 65;
  const height = 65;
  const sampler: HeightSampler = {
    width,
    height,
    data: new Float32Array(width * height),
    worldSize: WORLD,
    maxHeight: 100,
  };
  for (let z = 0; z < height; z++) {
    for (let x = 0; x < width; x++) {
      const lz = (z / (height - 1) - 0.5) * WORLD;
      const h = lz < 0 ? SOUTH_H : NORTH_H;
      sampler.data![z * width + x] = h / 100;
    }
  }

  getTerrainContext(state).set(terrain, {
    initialized: true,
    worldOffset: { x: 0, z: 0 },
    sampler,
    density: undefined,
  } as never);
  return state;
}

function species(
  id: string,
  where: SpeciesRule['where'],
  overrides?: Partial<SpeciesRule>
): SpeciesRule {
  return {
    id,
    weight: 1,
    cap: 0,
    url: `/unit/${id}.glb`,
    where,
    profile: 'none',
    spawnAttrs: {},
    ...overrides,
  };
}

function plan(
  speciesList: SpeciesRule[],
  overrides?: Partial<NatureRulesPlan>
): NatureRulesPlan {
  return {
    seed: 42,
    regionMin: [-100, 0, -100],
    regionMax: [100, 0, 100],
    spawnCountMode: 'fixed',
    count: 150,
    densityPerKm2: 0,
    minSpacing: 2.5,
    noiseScale: 90,
    species: speciesList,
    groves: [],
    ...overrides,
  };
}

function allPoints(
  buckets: Map<string, Array<[number, number]>>
): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  for (const pts of buckets.values()) out.push(...pts);
  return out;
}

function minPairDistance(points: Array<[number, number]>): number {
  let min = Infinity;
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const d = Math.hypot(
        points[i]![0] - points[j]![0],
        points[i]![1] - points[j]![1]
      );
      if (d < min) min = d;
    }
  }
  return min;
}

describe('planNatureSpawns', () => {
  let state: State;
  beforeEach(() => {
    state = buildState();
  });

  it('is deterministic for the same seed and world', () => {
    const p = plan([
      species('oak', { altitude: { min: 0, max: 10 } }),
      species('pine', { altitude: { min: 16, max: Infinity } }),
    ]);
    const a = planNatureSpawns(state, p);
    const b = planNatureSpawns(buildState(), p);
    expect(a.buckets).toEqual(b.buckets);
  });

  it('splits species by altitude zone', () => {
    const result = planNatureSpawns(
      state,
      plan([
        species('oak', { altitude: { min: 0, max: 10 } }),
        species('pine', { altitude: { min: 16, max: Infinity } }),
      ])
    );
    const oaks = result.buckets.get('oak')!;
    const pines = result.buckets.get('pine')!;
    expect(oaks.length).toBeGreaterThan(10);
    expect(pines.length).toBeGreaterThan(10);
    // The rule is on sampled altitude (lattice smoothing blurs the step
    // edge), not on which side of z=0 the point falls.
    for (const [x, z] of oaks) {
      expect(
        sampleTerrainSurface(state, x, z, 0.75, false)!.worldY
      ).toBeLessThanOrEqual(10 + 1e-6);
    }
    for (const [x, z] of pines) {
      expect(
        sampleTerrainSurface(state, x, z, 0.75, false)!.worldY
      ).toBeGreaterThanOrEqual(16 - 1e-6);
    }
  });

  it('respects min-spacing across every planned point', () => {
    const result = planNatureSpawns(
      state,
      plan([
        species('oak', { altitude: { min: 0, max: 10 } }),
        species('pine', { altitude: { min: 16, max: Infinity } }),
      ])
    );
    expect(minPairDistance(allPoints(result.buckets))).toBeGreaterThanOrEqual(
      2.5 - 1e-6
    );
  });

  it('honours per-species caps', () => {
    const result = planNatureSpawns(
      state,
      plan([species('oak', {}, { cap: 20 })])
    );
    expect(result.buckets.get('oak')!.length).toBe(20);
  });

  it('weighted pick gives both species a share', () => {
    const result = planNatureSpawns(
      state,
      plan([
        species('common', {}, { weight: 9 }),
        species('rare', {}, { weight: 1 }),
      ])
    );
    const common = result.buckets.get('common')!.length;
    const rare = result.buckets.get('rare')!.length;
    expect(common + rare).toBe(150);
    expect(common).toBeGreaterThan(rare);
    expect(rare).toBeGreaterThan(0);
  });

  it('keeps candidates out of registered footprints (SpawnExclusion path)', () => {
    // Disc covering the south half: every oak must fall outside it.
    registerSpawnFootprint(state, 0, -50, 80);
    const result = planNatureSpawns(
      state,
      plan([species('oak', {}, { weight: 9 }), species('pine', {})])
    );
    for (const [x, z] of result.buckets.get('oak')!) {
      expect(Math.hypot(x - 0, z + 50)).toBeGreaterThan(80 - 1e-6);
    }
  });

  it('road-dist band keeps species off the corridor margin', () => {
    registerGroundBrush(state, {
      kind: 'road',
      minX: -100,
      maxX: 100,
      minZ: -20,
      maxZ: 20,
      halfWidth: 4,
      carveHalfWidth: 16,
      path: [-100, 0, 100, 0],
    });
    const result = planNatureSpawns(
      state,
      plan([species('pine', { roadDist: { min: 20, max: Infinity } })])
    );
    const pines = result.buckets.get('pine')!;
    expect(pines.length).toBeGreaterThan(5);
    for (const [, z] of pines) {
      expect(Math.abs(z)).toBeGreaterThanOrEqual(36 - 1e-6);
    }
  });

  it('water-dist band places shore species in the annulus around the waterline', () => {
    registerWaterBody(state, {
      kind: 'lake',
      x: 50,
      z: 50,
      radius: 30,
      shoreRadius: 20,
      carveRadius: 34,
      waterY: 2,
    });
    const result = planNatureSpawns(
      state,
      plan([species('reed', { waterDist: { min: 0, max: 10 } })])
    );
    const reeds = result.buckets.get('reed')!;
    expect(reeds.length).toBeGreaterThan(3);
    for (const [x, z] of reeds) {
      const d = Math.hypot(x - 50, z - 50);
      expect(d).toBeGreaterThanOrEqual(20 - 6); // lattice smoothing tolerance
      expect(d).toBeLessThanOrEqual(30 + 6);
    }
  });

  it('groves compose mixed species around shared hubs', () => {
    const result = planNatureSpawns(
      state,
      plan(
        [
          species('hut', { slope: { min: 0, max: 30 } }, { weight: 0 }),
          species('crate', { slope: { min: 0, max: 30 } }, { weight: 0 }),
        ],
        {
          count: 0,
          groves: [
            {
              id: 'camp',
              count: 4,
              radius: 9,
              where: { slope: { min: 0, max: 30 } },
              members: [
                {
                  species: 'hut',
                  countMin: 1,
                  countMax: 1,
                  ringMin: 0,
                  ringMax: 0,
                },
                {
                  species: 'crate',
                  countMin: 2,
                  countMax: 3,
                  ringMin: 0.5,
                  ringMax: 1,
                },
              ],
            },
          ],
        }
      )
    );
    const huts = result.buckets.get('hut')!;
    const crates = result.buckets.get('crate')!;
    // Hub count is deterministic on the seed; every hub plants exactly one hut.
    expect(huts.length).toBeGreaterThanOrEqual(3);
    expect(crates.length).toBeGreaterThanOrEqual(huts.length * 2);
    expect(crates.length).toBeLessThanOrEqual(huts.length * 3);
    // Every crate sits inside the grove radius of some hut (its own hub).
    for (const [cx, cz] of crates) {
      const nearHut = huts.some(
        ([hx, hz]) => Math.hypot(cx - hx, cz - hz) <= 9 + 1e-6
      );
      expect(nearHut).toBe(true);
    }
  });

  it('near species get their own budget beside their hosts', () => {
    const result = planNatureSpawns(
      state,
      plan([
        species('oak', { altitude: { min: 0, max: 10 } }, { weight: 3 }),
        species(
          'mushroom',
          {
            nearSpecies: ['oak'],
            nearDist: { min: 0, max: 9 },
            altitude: { min: 0, max: 10 },
          },
          { weight: 1 }
        ),
      ])
    );
    const oaks = result.buckets.get('oak')!;
    const mushrooms = result.buckets.get('mushroom')!;
    expect(mushrooms.length).toBeGreaterThan(5);
    for (const [mx, mz] of mushrooms) {
      const nearOak = oaks.some(
        ([ox, oz]) => Math.hypot(mx - ox, mz - oz) <= 9 + 1e-6
      );
      expect(nearOak).toBe(true);
      // Mushrooms stay in the oak altitude zone.
      expect(
        sampleTerrainSurface(state, mx, mz, 0.75, false)!.worldY
      ).toBeLessThanOrEqual(10 + 1e-6);
    }
  });
});
