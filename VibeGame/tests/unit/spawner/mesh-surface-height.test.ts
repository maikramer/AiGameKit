import { describe, expect, it } from 'bun:test';
import { sampleMeshSurfaceHeight } from 'vibegame';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';

/** A fine heightmap with a ridge that falls *between* the coarse mesh
 * lattice vertices, so the rendered surface can't represent it — exactly the
 * case that made trees float. worldSize 8, texel spacing 1 in local space. */
function spikySampler(): HeightSampler {
  const width = 9;
  const height = 9;
  const data = new Float32Array(width * height); // mostly 0
  // Ridge along texel column 6 → local x ≈ 2, midway between the res=2 lattice
  // vertices at local x ∈ {0, 4}.
  for (let z = 0; z < height; z++) data[z * width + 6] = 1;
  return { width, height, data, worldSize: 8, maxHeight: 10 };
}

describe('sampleMeshSurfaceHeight', () => {
  it('flat sampler stays at 0', () => {
    const flat: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 8,
      maxHeight: 10,
    };
    expect(sampleMeshSurfaceHeight(flat, 1.3, -2.1, 64)).toBe(0);
  });

  it('mesh surface sits below a ridge between vertices (no floating)', () => {
    const s = spikySampler();
    // baseResolution 2 → mesh vertices only at local x ∈ {-4, 0, 4}. The ridge
    // at local x ≈ 2 falls inside a flat triangle, so the rendered surface is
    // far lower than the analytic height — anchoring there stops the float.
    const px = 2;
    const pz = 0;
    const analytic = sampleHeightAt(s, px, pz);
    const mesh = sampleMeshSurfaceHeight(s, px, pz, 2);
    expect(analytic).toBeGreaterThan(5); // ridge really is high here
    expect(mesh).toBeLessThan(analytic);
  });

  it('agrees with analytic height on a vertex of the lattice', () => {
    const s = spikySampler();
    // local (4,0) is a lattice vertex for resolution 2 (step = 4).
    expect(sampleMeshSurfaceHeight(s, 4, 0, 2)).toBeCloseTo(
      sampleHeightAt(s, 4, 0),
      5
    );
  });

  it('reproduces analytic height when lattice matches heightmap', () => {
    // A smooth ramp sampled at full resolution: a fine lattice (res = width-1)
    // collapses back to the analytic bilinear value.
    const width = 5;
    const data = new Float32Array(width * width);
    for (let z = 0; z < width; z++) {
      for (let x = 0; x < width; x++) {
        data[z * width + x] = (x + z) / (2 * (width - 1));
      }
    }
    const s: HeightSampler = {
      width,
      height: width,
      data,
      worldSize: 8,
      maxHeight: 10,
    };
    const px = 1.234;
    const pz = -0.876;
    expect(sampleMeshSurfaceHeight(s, px, pz, width - 1)).toBeCloseTo(
      sampleHeightAt(s, px, pz),
      5
    );
  });
});
