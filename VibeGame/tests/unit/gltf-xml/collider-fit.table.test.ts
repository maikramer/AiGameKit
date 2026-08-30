import { describe, expect, it } from 'bun:test';
import { ColliderShape } from 'aigamekit-vibegame/physics';
import {
  fitColliderFromAabb,
  GLTF_DYNAMIC_MIN_HALF_DIM,
} from '../../../src/plugins/gltf-xml/gltf-dynamic-collider-fit';

describe('fitColliderFromAabb box — scale sweep', () => {
  for (let sx = 1; sx <= 6; sx += 1) {
    for (let sy = 1; sy <= 4; sy += 1) {
      for (let sz = 1; sz <= 3; sz += 1) {
        it(`world ${sx}x${sy}x${sz} unit scale`, () => {
          const f = fitColliderFromAabb(ColliderShape.Box, sx, sy, sz, 1, 1, 1);
          expect(f.shape).toBe(ColliderShape.Box);
          expect(f.sizeX).toBeCloseTo(sx, 6);
          expect(f.sizeY).toBeCloseTo(sy, 6);
          expect(f.sizeZ).toBeCloseTo(sz, 6);
        });
      }
    }
  }
});

describe('fitColliderFromAabb box — non-uniform transform scale', () => {
  const scales: Array<[number, number, number]> = [
    [2, 1, 1],
    [1, 2, 1],
    [1, 1, 2],
    [0.5, 0.5, 0.5],
    [3, 2, 4],
  ];
  const world = { sx: 4, sy: 6, sz: 2 };

  for (const [tsx, tsy, tsz] of scales) {
    it(`tsx=${tsx} tsy=${tsy} tsz=${tsz}`, () => {
      const f = fitColliderFromAabb(
        ColliderShape.Box,
        world.sx,
        world.sy,
        world.sz,
        tsx,
        tsy,
        tsz
      );
      expect(f.sizeX).toBeCloseTo(world.sx / Math.abs(tsx), 5);
      expect(f.sizeY).toBeCloseTo(world.sy / Math.abs(tsy), 5);
      expect(f.sizeZ).toBeCloseTo(world.sz / Math.abs(tsz), 5);
    });
  }
});

describe('fitColliderFromAabb sphere — diagonal radius', () => {
  for (let k = 1; k <= 8; k += 1) {
    it(`cube edge ${k}`, () => {
      const f = fitColliderFromAabb(ColliderShape.Sphere, k, k, k, 1, 1, 1);
      const R = 0.5 * Math.sqrt(3 * k * k);
      expect(f.shape).toBe(ColliderShape.Sphere);
      expect(f.sizeX).toBeCloseTo(2 * R, 5);
    });
  }
});

describe('fitColliderFromAabb capsule — segment height', () => {
  for (let sy = 0.5; sy <= 4; sy += 0.5) {
    it(`sy=${sy} unit footprint`, () => {
      const f = fitColliderFromAabb(ColliderShape.Capsule, 1, sy, 1, 1, 1, 1);
      expect(f.shape).toBe(ColliderShape.Capsule);
      expect(f.radius).toBeGreaterThanOrEqual(GLTF_DYNAMIC_MIN_HALF_DIM);
      expect(f.height).toBeGreaterThanOrEqual(0);
      const r = Math.min(0.5, sy / 2);
      expect(f.radius).toBeCloseTo(Math.max(r, GLTF_DYNAMIC_MIN_HALF_DIM), 5);
    });
  }
});

describe('fitColliderFromAabb unknown shape → box', () => {
  for (const bad of [3, 7, 42, 255]) {
    it(`shape code ${bad}`, () => {
      const f = fitColliderFromAabb(bad, 2, 3, 4, 1, 1, 1);
      expect(f.shape).toBe(ColliderShape.Box);
      expect(f.sizeX).toBe(2);
      expect(f.sizeY).toBe(3);
      expect(f.sizeZ).toBe(4);
    });
  }
});
