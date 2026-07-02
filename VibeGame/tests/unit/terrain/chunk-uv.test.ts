import { describe, expect, it } from 'bun:test';
import { buildChunkGeometry } from '../../../src/plugins/terrain/chunk-geometry';
import { createFlatSampler } from '../../../src/plugins/terrain/height-sampler';

const sampler = createFlatSampler(1000, 0);

function uvAt(
  geo: ReturnType<typeof buildChunkGeometry>,
  index: number
): [number, number] {
  const uv = geo.getAttribute('uv');
  return [uv.getX(index), uv.getY(index)];
}

describe('buildChunkGeometry world-space UVs', () => {
  it('legacy per-chunk UVs when tileSize is 0 (back-compat)', () => {
    const geo = buildChunkGeometry(sampler, 0, 0, 100, 4, 0, 1, 0);
    expect(uvAt(geo, 0)).toEqual([0, 0]);
    expect(uvAt(geo, 4)).toEqual([1, 0]);
  });

  it('UV = fieldLocalXZ / tileSize, independent of chunk size', () => {
    const tile = 8;
    // chunk centred at (50, 0), size 100 → spans x ∈ [0, 100]
    const geo = buildChunkGeometry(sampler, 50, 0, 100, 4, 0, 1, tile);
    expect(uvAt(geo, 0)[0]).toBeCloseTo(0 / tile, 5);
    expect(uvAt(geo, 4)[0]).toBeCloseTo(100 / tile, 5);
  });

  it('texel density is continuous across a LOD boundary', () => {
    const tile = 8;
    // Fine chunk [0,100] (res 8) next to coarse chunk [100,300] (res 4):
    // the shared edge x=100 must have the same UV on both meshes, and the
    // per-metre UV rate must be identical on both sides.
    const fine = buildChunkGeometry(sampler, 50, 0, 100, 8, 0, 1, tile);
    const coarse = buildChunkGeometry(sampler, 200, 0, 200, 4, 0, 1, tile);

    const fineEdgeU = uvAt(fine, 8)[0]; // last vertex of first row (x=100)
    const coarseEdgeU = uvAt(coarse, 0)[0]; // first vertex (x=100)
    expect(fineEdgeU).toBeCloseTo(coarseEdgeU, 5);
    expect(fineEdgeU).toBeCloseTo(100 / tile, 5);

    // density: du per metre = 1/tile on both chunks despite different sizes
    const fineDu = (uvAt(fine, 1)[0] - uvAt(fine, 0)[0]) / (100 / 8);
    const coarseDu = (uvAt(coarse, 1)[0] - uvAt(coarse, 0)[0]) / (200 / 4);
    expect(fineDu).toBeCloseTo(1 / tile, 6);
    expect(coarseDu).toBeCloseTo(1 / tile, 6);
  });

  it('skirt vertices copy the world-space UV of their grid vertex', () => {
    const tile = 4;
    const res = 4;
    const geo = buildChunkGeometry(sampler, 0, 0, 40, res, 2, 1, tile);
    const gridCount = (res + 1) * (res + 1);
    // top skirt strip mirrors the first grid row
    for (let k = 0; k <= res; k++) {
      expect(uvAt(geo, gridCount + k)).toEqual(uvAt(geo, k));
    }
  });
});
