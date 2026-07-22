import { describe, expect, it } from 'bun:test';
import {
  getVariationPreset,
  hashWorldXZ,
  resolveVariationSpec,
  sampleVariation,
} from 'vibegame';

function mulberry32(seed: number): () => number {
  let t = seed >>> 0;
  return () => {
    t += 0x6d2b79f5;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
}

const GEOM = {
  randomYaw: true,
  scaleDistribution: 'linear' as const,
  scaleDiscreteValues: [] as number[],
  scaleMin: 0.7,
  scaleMax: 1.4,
  scaleAxisMin: 0.9,
  scaleAxisMax: 1.1,
  yawDistribution: 'linear' as const,
  yawDiscreteDeg: [] as number[],
};

describe('spawn-variation', () => {
  it('hashWorldXZ is stable and in [0,1)', () => {
    const a = hashWorldXZ(12.3, -4.5);
    const b = hashWorldXZ(12.3, -4.5);
    expect(a).toBe(b);
    expect(a).toBeGreaterThanOrEqual(0);
    expect(a).toBeLessThan(1);
  });

  it('foliage preset has brightness/contrast ranges', () => {
    const p = getVariationPreset('foliage');
    expect(p.brightnessMin).toBeLessThan(p.brightnessMax);
    expect(p.contrastMin).toBeLessThan(p.contrastMax);
    expect(p.hueJitterDeg).toBeGreaterThan(0);
  });

  it('resolveVariationSpec uses profile hint and XML overrides', () => {
    const base = resolveVariationSpec({}, 'tree');
    expect(base.preset).toBe('tree');
    const over = resolveVariationSpec(
      {
        variation: 'rock',
        'brightness-min': '0.5',
        'brightness-max': '0.6',
      },
      'tree'
    );
    expect(over.preset).toBe('rock');
    expect(over.brightnessMin).toBe(0.5);
    expect(over.brightnessMax).toBe(0.6);
  });

  it('sampleVariation is deterministic for the same seed+position', () => {
    const visual = getVariationPreset('foliage');
    const a = sampleVariation(GEOM, visual, mulberry32(42), 10, 20);
    const b = sampleVariation(GEOM, visual, mulberry32(42), 10, 20);
    expect(a.scaleUniform).toBe(b.scaleUniform);
    expect(a.yawRad).toBe(b.yawRad);
    expect(a.brightness).toBe(b.brightness);
    expect(a.contrast).toBe(b.contrast);
    expect(a.colorR).toBe(b.colorR);
  });

  it('sampleVariation none keeps identity visuals', () => {
    const visual = getVariationPreset('none');
    const s = sampleVariation(GEOM, visual, mulberry32(7), 0, 0);
    expect(s.colorR).toBe(1);
    expect(s.colorG).toBe(1);
    expect(s.colorB).toBe(1);
    expect(s.brightness).toBe(1);
    expect(s.contrast).toBe(1);
    expect(s.scaleUniform).toBeGreaterThanOrEqual(0.7);
    expect(s.scaleUniform).toBeLessThanOrEqual(1.4);
  });

  it('sampleVariation foliage keeps brightness/contrast in preset range', () => {
    const visual = getVariationPreset('foliage');
    for (let i = 0; i < 32; i++) {
      const s = sampleVariation(GEOM, visual, mulberry32(100 + i), i, -i);
      expect(s.brightness).toBeGreaterThanOrEqual(visual.brightnessMin - 1e-9);
      expect(s.brightness).toBeLessThanOrEqual(visual.brightnessMax + 1e-9);
      expect(s.contrast).toBeGreaterThanOrEqual(visual.contrastMin - 1e-9);
      expect(s.contrast).toBeLessThanOrEqual(visual.contrastMax + 1e-9);
    }
  });
});
