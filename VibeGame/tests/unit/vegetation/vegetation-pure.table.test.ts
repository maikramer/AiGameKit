import { describe, expect, it } from 'bun:test';
import {
  classifyVegetationRole,
  parseMeshRoleOverrides,
} from '../../../src/plugins/vegetation/roles';
import {
  parseVegetationMeshes,
  toBoolAttr,
} from '../../../src/plugins/vegetation/parse-meshes';
import {
  applyPatchScaleOverride,
  sizeTierFromFilename,
  sizeTierFromHeight,
} from '../../../src/plugins/vegetation/size-tier';
import { buildVegetationPlan } from '../../../src/plugins/vegetation/plan';

describe('parseVegetationMeshes', () => {
  const inputs: Array<{ raw: unknown; want: string[] }> = [
    { raw: 'a.glb b.glb', want: ['a.glb', 'b.glb'] },
    { raw: 'a.glb,b.glb', want: ['a.glb', 'b.glb'] },
    { raw: '  one.glb  ', want: ['one.glb'] },
    { raw: '', want: [] },
    { raw: null, want: [] },
    { raw: ['x.glb', 'y.glb'], want: ['x.glb', 'y.glb'] },
  ];

  for (const { raw, want } of inputs) {
    it(`parses ${JSON.stringify(raw)}`, () => {
      expect(parseVegetationMeshes(raw)).toEqual(want);
    });
  }

  for (let i = 0; i < 20; i += 1) {
    it(`token list length ${i}`, () => {
      const urls = Array.from({ length: i }, (_, k) => `/m/${k}.glb`);
      expect(parseVegetationMeshes(urls.join(' '))).toEqual(urls);
    });
  }
});

describe('toBoolAttr', () => {
  const truthy = ['1', 'true', 'yes', 'TRUE', ' Yes '];
  const falsy = ['0', 'false', 'no', 'FALSE', ' No '];

  for (const s of truthy) {
    it(`truthy "${s}" with fallback false`, () => {
      expect(toBoolAttr(s, false)).toBe(true);
    });
  }
  for (const s of falsy) {
    it(`falsy "${s}" with fallback true`, () => {
      expect(toBoolAttr(s, true)).toBe(false);
    });
  }
  for (const n of [0, 1, 2, -1]) {
    it(`number ${n}`, () => {
      expect(toBoolAttr(n, false)).toBe(n !== 0);
    });
  }
});

describe('classifyVegetationRole', () => {
  const cases: Array<{ url: string; role: 'grass' | 'flower' | 'plant' }> = [
    { url: '/grass_clump.glb', role: 'grass' },
    { url: '/meadow_flower_red.glb', role: 'flower' },
    { url: '/fern_patch.glb', role: 'plant' },
    { url: '/rock.glb', role: 'plant' },
    { url: '/WEED_small.glb', role: 'plant' },
  ];

  for (const { url, role } of cases) {
    it(url, () => {
      expect(classifyVegetationRole(url)).toBe(role);
    });
  }

  for (let i = 0; i < 15; i += 1) {
    it(`override grass for prop-${i}`, () => {
      const url = `/custom/prop-${i}.glb`;
      const map = parseMeshRoleOverrides(`${url}:grass`);
      expect(classifyVegetationRole(url, map)).toBe('grass');
    });
  }
});

describe('parseMeshRoleOverrides', () => {
  it('ignores malformed segments', () => {
    const m = parseMeshRoleOverrides('nocolon,bad:role,/ok.glb:flower');
    expect(m.get('/ok.glb')).toBe('flower');
    expect(m.size).toBe(1);
  });

  for (const role of ['grass', 'flower', 'plant'] as const) {
    it(`accepts role ${role}`, () => {
      const m = parseMeshRoleOverrides(`/a.glb:${role}`);
      expect(m.get('/a.glb')).toBe(role);
    });
  }
});

describe('sizeTierFromHeight', () => {
  for (let h = 0.05; h <= 0.6; h += 0.05) {
    it(`height ${h.toFixed(2)}m`, () => {
      const tier = sizeTierFromHeight(h);
      if (h < 0.22) expect(tier).toBe('small');
      else if (h <= 0.35) expect(tier).toBe('medium');
      else expect(tier).toBe('large');
    });
  }
});

describe('sizeTierFromFilename hints', () => {
  for (const [name, tier] of [
    ['tree_large.glb', 'large'],
    ['bush_tall.glb', 'large'],
    ['grass_small.glb', 'small'],
    ['plain.glb', null],
  ] as const) {
    it(name, () => {
      expect(sizeTierFromFilename(name)).toBe(tier);
    });
  }
});

describe('applyPatchScaleOverride', () => {
  const tier = {
    tier: 'medium' as const,
    scaleMin: 1,
    scaleMax: 1.8,
    heightM: 0.28,
  };

  for (const [lo, hi, wantMin, wantMax] of [
    [null, null, 1, 1.8],
    [0.5, 2, 0.5, 2],
    [3, 1, 1, 3],
  ] as const) {
    it(`patch ${lo}-${hi}`, () => {
      const r = applyPatchScaleOverride(tier, lo, hi);
      expect(r.scaleMin).toBe(wantMin);
      expect(r.scaleMax).toBe(wantMax);
    });
  }
});

describe('buildVegetationPlan smart layering', () => {
  const baseInput = {
    meshes: ['/grass.glb', '/flower.glb', '/plant.glb'],
    smart: true,
    seed: 1,
    regionMin: [-10, 0, -10] as [number, number, number],
    regionMax: [10, 0, 10] as [number, number, number],
    clusterCount: 10,
    clusterRadius: 3,
    flowerNearRadius: 2,
    flowerDensityRatio: 0.2,
    plantDensityRatio: 0.3,
    wind: true,
    avoidWater: true,
    avoidOverlaps: false,
    maxSlopeDeg: 30,
    maxDistance: 100,
    footprintRadius: 0.2,
    spawnCountMode: 'density' as const,
    densityPerKm2: 1000,
    count: 0,
    patchScaleMin: null,
    patchScaleMax: null,
    scaleAxisMin: 0.9,
    scaleAxisMax: 1.1,
    variation: {},
  };

  it('smart multi-role yields 3 layers', () => {
    const plan = buildVegetationPlan(baseInput);
    expect(plan.smart).toBe(true);
    expect(plan.layers.length).toBe(3);
    expect(plan.allMeshes).toEqual(baseInput.meshes);
  });

  for (const smart of [false, true]) {
    it(`single mesh smart=${smart}`, () => {
      const plan = buildVegetationPlan({
        ...baseInput,
        smart,
        meshes: ['/only_grass.glb'],
      });
      expect(plan.smart).toBe(false);
      expect(plan.layers.length).toBe(1);
    });
  }
});
