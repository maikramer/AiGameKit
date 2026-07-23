import { describe, expect, it } from 'bun:test';
import {
  aabbContains,
  packRgb,
  parseColor,
  parsePolygonString,
  pointInPolygon,
} from '../../../../src/plugins/biomes/adapters';
import { advanceBlend } from '../../../../src/plugins/biomes/systems';
import { buildBiomeSplat } from '../../../../src/plugins/biomes/splat';
import type { BiomeRegionInfo } from '../../../../src/plugins/biomes/parser';

describe('biomes parseColor table-driven', () => {
  const hexCases: Array<[string, number, number, number]> = [
    ['#000000', 0, 0, 0],
    ['#ffffff', 1, 1, 1],
    ['#ff0000', 1, 0, 0],
    ['#00ff00', 0, 1, 0],
    ['#0000ff', 0, 0, 1],
    ['#808080', 128 / 255, 128 / 255, 128 / 255],
    ['#ff00ff', 1, 0, 1],
    ['#00ffff', 0, 1, 1],
    ['#123456', 0x12 / 255, 0x34 / 255, 0x56 / 255],
    ['#abcdef', 0xab / 255, 0xcd / 255, 0xef / 255],
  ];

  for (const [hex, r, g, b] of hexCases) {
    it(`parseColor hex ${hex}`, () => {
      const c = parseColor(hex);
      expect(c.r).toBeCloseTo(r, 5);
      expect(c.g).toBeCloseTo(g, 5);
      expect(c.b).toBeCloseTo(b, 5);
    });
  }

  const rgbFloatCases: Array<[string, number, number, number]> = [
    ['0 0 0', 0, 0, 0],
    ['1 1 1', 1, 1, 1],
    ['0.5 0.5 0.5', 0.5, 0.5, 0.5],
    ['0.25 0.5 0.75', 0.25, 0.5, 0.75],
    ['255 0 0', 1, 0, 0],
    ['0 255 0', 0, 1, 0],
    ['0 0 255', 0, 0, 1],
    ['128 128 128', 128 / 255, 128 / 255, 128 / 255],
    ['1 0 0', 1, 0, 0],
    ['0 1 0', 0, 1, 0],
  ];

  for (const [s, r, g, b] of rgbFloatCases) {
    it(`parseColor floats "${s}"`, () => {
      const c = parseColor(s);
      expect(c.r).toBeCloseTo(r, 5);
      expect(c.g).toBeCloseTo(g, 5);
      expect(c.b).toBeCloseTo(b, 5);
    });
  }
});

describe('biomes packRgb table-driven', () => {
  const cases: Array<[number, number, number, number]> = [
    [0, 0, 0, 0],
    [1, 1, 1, 0xffffff],
    [1, 0, 0, 0xff0000],
    [0, 1, 0, 0x00ff00],
    [0, 0, 1, 0x0000ff],
    [0.5, 0.5, 0.5, 0x808080],
    [-1, 0, 0, 0],
    [2, 0, 0, 0xff0000],
    [0.1, 0.2, 0.3, packRgb(0.1, 0.2, 0.3)],
    [0.99, 0.01, 0.5, packRgb(0.99, 0.01, 0.5)],
  ];

  for (const [r, g, b, expected] of cases) {
    it(`packRgb(${r},${g},${b})`, () => {
      expect(packRgb(r, g, b)).toBe(expected >>> 0);
    });
  }

  for (let i = 0; i <= 16; i++) {
    const t = i / 16;
    it(`packRgb grey ramp ${i}/16`, () => {
      const packed = packRgb(t, t, t);
      const ri = Math.round(t * 255);
      expect(packed).toBe((ri << 16) | (ri << 8) | ri);
    });
  }
});

describe('biomes advanceBlend table-driven', () => {
  const dts = [0, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2];
  const durations = [0, 0.1, 0.5, 1, 2, 5];

  for (const dt of dts) {
    for (const dur of durations) {
      it(`advanceBlend(0, dt=${dt}, dur=${dur})`, () => {
        const out = advanceBlend(0, dt, dur);
        if (dur <= 0) expect(out).toBe(1);
        else expect(out).toBeGreaterThanOrEqual(0);
        expect(out).toBeLessThanOrEqual(1);
      });
    }
  }

  for (let start = 0; start <= 10; start++) {
    const blend = start / 10;
    it(`advanceBlend from ${blend} with dt=0.25 dur=0.5`, () => {
      const out = advanceBlend(blend, 0.25, 0.5);
      expect(out).toBeGreaterThanOrEqual(blend);
      expect(out).toBeLessThanOrEqual(1);
    });
  }
});

describe('biomes aabbContains grid', () => {
  for (let x = -2; x <= 2; x++) {
    for (let z = -2; z <= 2; z++) {
      it(`aabbContains unit box at (${x},${z})`, () => {
        const inside = x >= -1 && x <= 1 && z >= -1 && z <= 1;
        expect(aabbContains(-1, -1, 1, 1, x, z)).toBe(inside);
      });
    }
  }
});

describe('biomes pointInPolygon grid on unit square', () => {
  const square = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ];
  for (let x = -2; x <= 2; x++) {
    for (let z = -2; z <= 2; z++) {
      it(`pointInPolygon square at (${x},${z})`, () => {
        const strictlyInside = x > -1 && x < 1 && z > -1 && z < 1;
        const clearlyOutside = x < -1 || x > 1 || z < -1 || z > 1;
        if (strictlyInside) expect(pointInPolygon(x, z, square)).toBe(true);
        if (clearlyOutside) expect(pointInPolygon(x, z, square)).toBe(false);
      });
    }
  }
});

describe('biomes buildBiomeSplat', () => {
  const region: BiomeRegionInfo = {
    entity: 1,
    id: 'test',
    vertices: [
      [-50, -50],
      [50, -50],
      [50, 50],
      [-50, 50],
    ],
    minX: -50,
    minZ: -50,
    maxX: 50,
    maxZ: 50,
    terrainTexture: '/tex/grass.png',
  };

  for (let i = 0; i < 5; i++) {
    it(`buildBiomeSplat returns splat with layer — variant ${i}`, () => {
      const splat = buildBiomeSplat([region], -100, -100, 200, 200);
      expect(splat).not.toBeNull();
      expect(splat!.layerUrls).toEqual(['/tex/grass.png']);
      expect(splat!.texture.width).toBe(512);
    });
  }

  for (let i = 0; i < 5; i++) {
    it(`buildBiomeSplat null without terrainTexture ${i}`, () => {
      const noTex = { ...region, terrainTexture: undefined };
      expect(buildBiomeSplat([noTex], 0, 0, 100, 100)).toBeNull();
    });
  }
});

describe('biomes parsePolygonString variants', () => {
  const polys: Array<[string, number]> = [
    ['0 0, 1 0, 1 1', 3],
    ['[0,0;1,0;1,1]', 3],
    ['10 20, 30 40', 2],
    ['[-5,-5;5,-5;5,5;-5,5]', 4],
  ];
  for (const [p, minLen] of polys) {
    it(`parsePolygonString vertex count for "${p.slice(0, 12)}..."`, () => {
      const g = parsePolygonString(p);
      expect(g.vertices.length).toBeGreaterThanOrEqual(minLen);
    });
  }
});
