import { describe, expect, it } from 'bun:test';
import { normalFromHeightSampler } from 'vibegame';
import { sampleTerrainHeightFromHeightmap } from '../../../src/plugins/terrain/utils';

describe('normalFromHeightSampler', () => {
  it('plano inclinado h = 0.1*x + 0.2*z', () => {
    const heightAt = (x: number, z: number) => 0.1 * x + 0.2 * z;
    const n = normalFromHeightSampler(heightAt, 3, -2, 0.5);
    expect(n.x).toBeCloseTo(-0.1 / Math.sqrt(0.01 + 1 + 0.04), 5);
    expect(n.y).toBeCloseTo(1 / Math.sqrt(1.05), 5);
    expect(n.z).toBeCloseTo(-0.2 / Math.sqrt(1.05), 5);
  });

  it('plano horizontal retorna ~+Y', () => {
    const heightAt = () => 5;
    const n = normalFromHeightSampler(heightAt, 0, 0, 1);
    expect(n.x).toBeCloseTo(0, 5);
    expect(n.y).toBeCloseTo(1, 5);
    expect(n.z).toBeCloseTo(0, 5);
  });
});

describe('sampleTerrainHeightFromHeightmap', () => {
  it('matchWebGL inverte V em relação ao sampling bruto (2x2)', () => {
    const data = new Uint8ClampedArray(2 * 2 * 4);
    data[0] = 255;
    data[1] = 0;
    data[2] = 0;
    data[3] = 255;
    data[4] = 255;
    data[5] = 0;
    data[6] = 0;
    data[7] = 255;
    data[8] = 0;
    data[9] = 0;
    data[10] = 0;
    data[11] = 255;
    data[12] = 0;
    data[13] = 0;
    data[14] = 0;
    data[15] = 255;
    const imageData = { width: 2, height: 2, data } as ImageData;
    const worldSize = 2;
    const maxHeight = 100;
    const wx = 0;
    const wz = -1;
    const hRaw = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      wx,
      wz,
      false,
      0,
      0
    );
    const hWeb = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      wx,
      wz,
      true,
      0,
      0
    );
    expect(hRaw).toBe(100);
    expect(hWeb).toBe(0);
  });

  it('subtrai origem do terreno (coordenadas locais)', () => {
    const data = new Uint8ClampedArray(2 * 2 * 4).fill(0);
    data[0] = 255;
    data[1] = 0;
    data[2] = 0;
    data[3] = 255;
    data[4] = 255;
    data[5] = 0;
    data[6] = 0;
    data[7] = 255;
    data[8] = 0;
    data[9] = 0;
    data[10] = 0;
    data[11] = 255;
    data[12] = 0;
    data[13] = 0;
    data[14] = 0;
    data[15] = 255;
    const imageData = { width: 2, height: 2, data } as ImageData;
    const h = sampleTerrainHeightFromHeightmap(
      imageData,
      2,
      100,
      10,
      -1,
      true,
      10,
      0
    );
    expect(h).toBe(0);
  });

  it('boxKernel 3 faz média 3×3 em texels (suaviza degraus)', () => {
    const data = new Uint8ClampedArray(3 * 3 * 4).fill(0);
    for (let i = 0; i < 9; i++) {
      data[i * 4] = 255;
      data[i * 4 + 3] = 255;
    }
    data[4 * 4] = 0;
    data[4 * 4 + 3] = 255;
    const imageData = { width: 3, height: 3, data } as ImageData;
    const worldSize = 3;
    const maxHeight = 90;
    const wx = 0;
    const wz = 0;
    const h1 = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      wx,
      wz,
      true,
      0,
      0,
      1
    );
    const h3 = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      wx,
      wz,
      true,
      0,
      0,
      3
    );
    expect(h1).toBe(0);
    expect(h3).toBeCloseTo((8 / 9) * maxHeight, 5);
  });
});
