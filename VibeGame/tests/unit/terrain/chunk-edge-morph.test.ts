import { describe, expect, it } from 'bun:test';
import { buildChunkGeometry } from '../../../src/plugins/terrain/chunk-geometry';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

/** Rolling hills so border normals are non-trivial. */
function hillySampler(): HeightSampler {
  const n = 65;
  const data = new Float32Array(n * n);
  const worldSize = 200;
  const half = worldSize / 2;
  for (let zi = 0; zi < n; zi++) {
    for (let xi = 0; xi < n; xi++) {
      const x = (xi / (n - 1)) * worldSize - half;
      const z = (zi / (n - 1)) * worldSize - half;
      data[zi * n + xi] =
        0.35 + 0.25 * Math.sin(x * 0.08) + 0.2 * Math.cos(z * 0.11);
    }
  }
  return { width: n, height: n, data, worldSize, maxHeight: 40 };
}

describe('buildChunkGeometry frontier normals', () => {
  it('shared edge verts have identical normals on both chunks', () => {
    const sampler = hillySampler();
    const eps = 200 / 1024;
    // East chunk origin 50, west chunk origin -50; shared field edge x=0.
    const east = buildChunkGeometry(sampler, 50, 0, 100, 8, 0, eps, 0);
    const west = buildChunkGeometry(sampler, -50, 0, 100, 8, 0, eps, 0);
    const eN = east.getAttribute('normal');
    const wN = west.getAttribute('normal');
    const verts = 9;
    for (let zi = 0; zi < verts; zi++) {
      const e = zi * verts + 0; // east chunk west column
      const w = zi * verts + 8; // west chunk east column
      expect(eN.getX(e)).toBeCloseTo(wN.getX(w), 5);
      expect(eN.getY(e)).toBeCloseTo(wN.getY(w), 5);
      expect(eN.getZ(e)).toBeCloseTo(wN.getZ(w), 5);
    }
  });

  it('does not push border verts outward (no overlap fade)', () => {
    const sampler = hillySampler();
    const geo = buildChunkGeometry(sampler, 50, 0, 100, 8, 0, 1, 0);
    const pos = geo.getAttribute('position');
    const verts = 9;
    // West column local X must stay at -half (-50), not pushed further out.
    for (let zi = 0; zi < verts; zi++) {
      expect(pos.getX(zi * verts + 0)).toBeCloseTo(-50, 5);
      expect(pos.getX(zi * verts + 8)).toBeCloseTo(50, 5);
    }
  });
});
