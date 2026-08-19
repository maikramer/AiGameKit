import { describe, expect, it } from 'bun:test';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';

/**
 * A slope built from a coarse lattice: bilinear taps make the derivative jump
 * at every texel boundary, which renders as one flat facet per texel. The
 * sampler blends toward Catmull-Rom so the normals stay continuous.
 */
function rampSampler(smoothing: number): HeightSampler {
  const n = 16;
  const data = new Float32Array(n * n);
  for (let j = 0; j < n; j++) {
    for (let i = 0; i < n; i++) {
      // Curved ramp — a pure plane is linear, so bilinear would look perfect.
      const t = i / (n - 1);
      data[j * n + i] = t * t;
    }
  }
  return { width: n, height: n, data, worldSize: 64, maxHeight: 20, smoothing };
}

/** Largest jump in slope between neighbouring steps — 0 for a C1 surface. */
function maxSlopeJump(sampler: HeightSampler): number {
  const step = 0.05;
  const slopes: number[] = [];
  for (let x = -20; x <= 20; x += step) {
    const a = sampleHeightAt(sampler, x, 0);
    const b = sampleHeightAt(sampler, x + step, 0);
    slopes.push((b - a) / step);
  }
  let worst = 0;
  for (let i = 1; i < slopes.length; i++) {
    worst = Math.max(worst, Math.abs(slopes[i]! - slopes[i - 1]!));
  }
  return worst;
}

describe('height sampler smoothing', () => {
  it('bilinear leaves visible derivative jumps at texel boundaries', () => {
    expect(maxSlopeJump(rampSampler(0))).toBeGreaterThan(0.02);
  });

  it('Catmull-Rom removes the faceting', () => {
    const smooth = maxSlopeJump(rampSampler(1));
    const bilinear = maxSlopeJump(rampSampler(0));
    expect(smooth).toBeLessThan(bilinear * 0.5);
  });

  it('defaults to smooth when the field is absent', () => {
    const s = rampSampler(1);
    delete (s as { smoothing?: number }).smoothing;
    expect(maxSlopeJump(s)).toBeLessThan(maxSlopeJump(rampSampler(0)) * 0.5);
  });

  it('tracks the underlying heights within the lattice range', () => {
    const s = rampSampler(1);
    // Centre of the field is mid-ramp; smoothing must not shift it wildly.
    expect(sampleHeightAt(s, 0, 0)).toBeCloseTo(
      sampleHeightAt(rampSampler(0), 0, 0),
      1
    );
  });

  it('clamps the 4x4 stencil at the borders instead of wrapping', () => {
    const s = rampSampler(1);
    const half = s.worldSize / 2;
    expect(sampleHeightAt(s, -half, -half)).toBeCloseTo(0, 3);
    expect(sampleHeightAt(s, half, half)).toBeCloseTo(s.maxHeight, 3);
  });
});
