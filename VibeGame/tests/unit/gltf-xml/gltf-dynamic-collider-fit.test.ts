import { describe, expect, it } from 'bun:test';
import { ColliderShape } from 'vibegame/physics';
import {
  fitColliderFromAabb,
  GLTF_DYNAMIC_MIN_HALF_DIM,
} from '../../../src/plugins/gltf-xml/gltf-dynamic-collider-fit';

describe('fitColliderFromAabb', () => {
  it('box: dimensões locais = mundo / escala por eixo', () => {
    const f = fitColliderFromAabb(ColliderShape.Box, 2, 4, 6, 2, 1, 0.5);
    expect(f.shape).toBe(ColliderShape.Box);
    expect(f.sizeX).toBe(1);
    expect(f.sizeY).toBe(4);
    expect(f.sizeZ).toBe(12);
  });

  it('sphere: diâmetro local a partir da diagonal do AABB', () => {
    const sx = 1;
    const sy = 1;
    const sz = 1;
    const R = 0.5 * Math.sqrt(sx * sx + sy * sy + sz * sz);
    const f = fitColliderFromAabb(ColliderShape.Sphere, sx, sy, sz, 1, 1, 1);
    expect(f.shape).toBe(ColliderShape.Sphere);
    expect(f.sizeX).toBeCloseTo(2 * R, 6);
    expect(f.sizeY).toBe(0);
    expect(f.sizeZ).toBe(0);
  });

  it('capsule: raio e segmento em metros mundo', () => {
    const f = fitColliderFromAabb(ColliderShape.Capsule, 1, 2, 1, 1, 1, 1);
    expect(f.shape).toBe(ColliderShape.Capsule);
    expect(f.radius).toBe(0.5);
    expect(f.height).toBe(1);
  });

  it('capsule: raio limitado por sy e mínimo', () => {
    const f = fitColliderFromAabb(ColliderShape.Capsule, 2, 0.02, 2, 1, 1, 1);
    expect(f.radius).toBeGreaterThanOrEqual(GLTF_DYNAMIC_MIN_HALF_DIM);
    expect(f.height).toBeGreaterThanOrEqual(0);
  });

  it('valor desconhecido cai em box', () => {
    const f = fitColliderFromAabb(99, 2, 2, 2, 1, 1, 1);
    expect(f.shape).toBe(ColliderShape.Box);
    expect(f.sizeX).toBe(2);
  });
});
