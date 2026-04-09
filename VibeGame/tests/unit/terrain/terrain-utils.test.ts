import { describe, expect, it } from 'bun:test';
import {
  resampleChunkHeightsForCollider,
  sampleTerrainHeightFromHeightmap,
} from '../../../src/plugins/terrain/utils';

function makeImageData(
  width: number,
  height: number,
  fillRed: number
): ImageData {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = fillRed;
    data[i + 1] = 0;
    data[i + 2] = 0;
    data[i + 3] = 255;
  }
  return { width, height, data } as ImageData;
}

describe('terrain utils', () => {
  it('sampleTerrainHeightFromHeightmap: flat heightmap gives maxHeight at center', () => {
    const imageData = makeImageData(4, 4, 255);
    const worldSize = 256;
    const maxHeight = 50;
    const h = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      0,
      0,
      true,
      0,
      0,
      1
    );
    expect(h).toBeCloseTo(maxHeight);
  });

  it('sampleTerrainHeightFromHeightmap: boxKernel 3 averages neighborhood', () => {
    const data = new Uint8ClampedArray(3 * 3 * 4);
    for (let i = 0; i < 9; i++) {
      const v = i === 4 ? 255 : 0;
      const o = i * 4;
      data[o] = v;
      data[o + 3] = 255;
    }
    const imageData = { width: 3, height: 3, data } as ImageData;
    const worldSize = 256;
    const maxHeight = 100;
    const h = sampleTerrainHeightFromHeightmap(
      imageData,
      worldSize,
      maxHeight,
      0,
      0,
      true,
      0,
      0,
      3
    );
    expect(h).toBeGreaterThan(0);
    expect(h).toBeLessThan(maxHeight);
  });

  it('resampleChunkHeightsForCollider fills heights from red channel', () => {
    const imageData = makeImageData(2, 2, 128);
    const worldSize = 256;
    const maxHeight = 40;
    const chunk = {
      position: { x: 0, y: 0, z: 0 },
      size: 32,
      rows: 2,
      cols: 2,
      heights: new Float32Array(4),
    };
    resampleChunkHeightsForCollider(
      chunk,
      worldSize,
      maxHeight,
      imageData,
      true
    );
    const expected = (128 / 255) * maxHeight;
    for (let i = 0; i < 4; i++) {
      expect(chunk.heights[i]).toBeCloseTo(expected, 5);
    }
  });
});
