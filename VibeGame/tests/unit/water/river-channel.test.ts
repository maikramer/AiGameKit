import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { RiverChannel } from '../../../src/plugins/water/river-channel';

function flatSampler(): HeightSampler {
  const data = new Float32Array(128 * 128).fill(0.5);
  return { width: 128, height: 128, data, worldSize: 100, maxHeight: 100 };
}

describe('RiverChannel', () => {
  it('computeAabb covers the path expanded by width/2', () => {
    const rc = new RiverChannel({
      path: [-40, 0, 40, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const a = rc.computeAabb();
    expect(a.minX).toBeLessThanOrEqual(-40);
    expect(a.maxX).toBeGreaterThanOrEqual(40);
    expect(a.minZ).toBeLessThanOrEqual(-3);
    expect(a.maxZ).toBeGreaterThanOrEqual(3);
  });

  it('carve lowers the sampler along the path and returns carved=true', () => {
    const s = flatSampler();
    const rc = new RiverChannel({
      path: [-40, 0, 40, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const before = sampleHeightAt(s, 0, 0);
    const result = rc.carve(s);
    const after = sampleHeightAt(s, 0, 0);
    expect(result.carved).toBe(true);
    expect(after).toBeLessThan(before);
  });

  it('worldOrigin returns (0, worldOffsetY, 0) — ribbon vertices carry X/Z and field-local Y', () => {
    const rc = new RiverChannel({
      path: [0, 0, 100, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    // The mesh only needs the field's world Y offset; per-node Y is in the vertices.
    expect(rc.worldOrigin(12)).toEqual({ x: 0, y: 12, z: 0 });
  });

  it('densityBoost returns 255', () => {
    const rc = new RiverChannel({
      path: [0, 0, 10, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    expect(rc.densityBoost()).toBe(255);
  });

  it('toWaterBody(worldWaterY) returns a river body with kind="river" and the given waterY', () => {
    const rc = new RiverChannel({
      path: [0, 0, 100, 0],
      width: 6,
      depth: 4,
      waterOffset: 0.3,
    });
    const body = rc.toWaterBody(5);
    expect(body.kind).toBe('river');
    if (body.kind === 'river') {
      expect(body.width).toBe(6);
      expect(body.waterY).toBe(5);
      expect(body.path.length).toBe(2);
    }
  });
});
