import { describe, expect, it } from 'bun:test';
import { terrainHeightsToRapierColumnMajor } from '../../../src/plugins/terrain/utils';

describe('terrain utils', () => {
  it('terrainHeightsToRapierColumnMajor transposes row-major to column-major', () => {
    const rowMajor = new Float32Array([
      1, 2, 3,
      4, 5, 6,
    ]);
    const result = terrainHeightsToRapierColumnMajor(rowMajor, 2, 3);
    expect(result.length).toBe(6);
    expect(result[0]).toBe(1);
    expect(result[1]).toBe(4);
    expect(result[2]).toBe(2);
    expect(result[3]).toBe(5);
    expect(result[4]).toBe(3);
    expect(result[5]).toBe(6);
  });

  it('terrainHeightsToRapierColumnMajor with square grid', () => {
    const rowMajor = new Float32Array([
      1, 2,
      3, 4,
    ]);
    const result = terrainHeightsToRapierColumnMajor(rowMajor, 2, 2);
    expect(result.length).toBe(4);
    expect(result[0]).toBe(1);
    expect(result[1]).toBe(3);
    expect(result[2]).toBe(2);
    expect(result[3]).toBe(4);
  });
});
