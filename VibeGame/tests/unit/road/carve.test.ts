import { describe, expect, it } from 'bun:test';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import { carveRoadCorridor } from '../../../src/plugins/road/carve';

/** Flat sampler at a constant normalized height; high-res by default to avoid bilinear bleed. */
function flatSampler(
  heightNorm: number,
  size = 512,
  world = 200
): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(heightNorm),
    worldSize: world,
    maxHeight: 100,
  };
}

/** Sampler with a single radial bump/depression at (x,z) field-local. */
function bumpSampler(
  base: number,
  bump: number,
  bx: number,
  bz: number,
  radius: number,
  size = 512,
  world = 200
): HeightSampler {
  const s = flatSampler(base, size, world);
  if (!s.data) return s;
  const half = world / 2;
  const step = world / (size - 1);
  const r2 = radius * radius;
  for (let zi = 0; zi < size; zi++) {
    const wz = zi * step - half;
    for (let xi = 0; xi < size; xi++) {
      const wx = xi * step - half;
      const d2 = (wx - bx) ** 2 + (wz - bz) ** 2;
      if (d2 <= r2) {
        // Cosine bump: peak at centre, 0 at radius.
        const t = 1 - d2 / r2;
        const k = (Math.cos(Math.PI * (1 - t)) + 1) / 2;
        s.data[zi * size + xi] = base + bump * k;
      }
    }
  }
  return s;
}

/**
 * Dense polyline along a straight segment from (x0,z0) to (x1,z1) at ~1 m
 * spacing. The carve samples the profile only at the given path stations, so a
 * 2-point path would miss features between them — the real RoadApplySystem
 * resamples before calling carve; tests must do the same.
 */
function densePath(x0: number, z0: number, x1: number, z1: number): number[] {
  const len = Math.hypot(x1 - x0, z1 - z0);
  const n = Math.max(2, Math.ceil(len));
  const path: number[] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    path.push(x0 + (x1 - x0) * t, z0 + (z1 - z0) * t);
  }
  return path;
}

describe('carveRoadCorridor', () => {
  it('returns false on a sampler with no data (flat/undecoded)', () => {
    const s: HeightSampler = {
      width: 1,
      height: 1,
      data: null,
      worldSize: 200,
      maxHeight: 100,
    };
    const changed = carveRoadCorridor(s, {
      path: [-20, 0, 20, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('returns false on a path shorter than 4 numbers (< 2 points)', () => {
    const s = flatSampler(0.5);
    const changed = carveRoadCorridor(s, {
      path: [0, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('returns false when the path AABB is entirely outside the field', () => {
    const s = flatSampler(0.5, 64, 200);
    const changed = carveRoadCorridor(s, {
      path: [5000, 0, 6000, 0],
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(false);
  });

  it('cuts a bump: the corridor centre drops toward the smoothed profile', () => {
    // Bump at origin, terrain base 50 m (0.5 norm), bump +20 m (0.2 norm).
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    // Dense path along Z through the bump so the profile sees the bump.
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(true);
    // Centre was 70 m before (0.5 + 0.2 = 0.7 → 70). After carve it must be
    // strictly lower than the bump peak (the profile smooths it toward the
    // surrounding 50 m) but above 50 (smoothing window keeps some of the bump).
    const h = sampleHeightAt(s, 0, 0);
    expect(h).toBeLessThan(70);
    expect(h).toBeGreaterThan(50);
  });

  it('fills a valley: the corridor centre rises toward the smoothed profile', () => {
    // Depression at origin: base 50 m, bump -20 m (0.2 → 0.3 norm = 30 m at centre).
    const s = bumpSampler(0.5, -0.2, 0, 0, 10);
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    expect(changed).toBe(true);
    // Centre was 30 m before; after carve it must be higher (filled toward 50)
    // but below 50 (smoothing window keeps some of the depression).
    const h = sampleHeightAt(s, 0, 0);
    expect(h).toBeGreaterThan(30);
    expect(h).toBeLessThan(50);
  });

  it('does not touch terrain beyond the lateral reach (width/2 + falloff)', () => {
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    const before = sampleHeightAt(s, 40, 0); // far from the path
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    const after = sampleHeightAt(s, 40, 0);
    expect(after).toBeCloseTo(before, 4);
  });

  it('runs once and mutates the sampler in place (single-pass design)', () => {
    // The carve is designed to run exactly once per road (RoadApplySystem
    // latches `applied=1`). It recomputes the profile from the current sampler
    // state, so it is not strictly idempotent — but a single pass must lower
    // the bump centre toward the surrounding terrain.
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    const before = sampleHeightAt(s, 0, 0);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    const after = sampleHeightAt(s, 0, 0);
    expect(after).toBeLessThan(before);
  });

  it('is a no-op on a perfectly flat sampler (profile == terrain, nothing to blend)', () => {
    const s = flatSampler(0.5);
    const changed = carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    // Flat terrain → profile is flat at 0.5 → target == data everywhere → no change.
    expect(changed).toBe(false);
  });

  it('smooths the longitudinal profile: a single-station spike is averaged down', () => {
    // Narrow spike (radius 2) at the midpoint of a long path through flat terrain.
    const s = bumpSampler(0.5, 0.3, 0, 0, 2);
    carveRoadCorridor(s, {
      path: densePath(0, -30, 0, 30),
      width: 4,
      falloff: 2,
      window: 20, // wide window → strong smoothing
    });
    const h = sampleHeightAt(s, 0, 0);
    // Without smoothing the spike (80 m) would be the target; with a 20 m window
    // the profile averages it toward the surrounding 50 m.
    expect(h).toBeLessThan(80);
  });

  it('lowers terrain inside the corridor and preserves it outside', () => {
    // Bump at origin; path along Z through it.
    const s = bumpSampler(0.5, 0.2, 0, 0, 10);
    carveRoadCorridor(s, {
      path: densePath(0, -40, 0, 40),
      width: 6,
      falloff: 2,
      window: 8,
    });
    // Inside corridor (x=0): lowered (was ~70, now < 70).
    expect(sampleHeightAt(s, 0, 0)).toBeLessThan(70);
    // Outside reach (x=40): untouched (was 50, still 50).
    expect(sampleHeightAt(s, 40, 0)).toBeCloseTo(50, 2);
  });
});
