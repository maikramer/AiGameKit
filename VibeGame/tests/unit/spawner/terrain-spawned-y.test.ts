import { describe, expect, it } from 'bun:test';
import { sinkOffsetForSlope } from '../../../src/plugins/spawner/surface';

describe('edge-sink policy', () => {
  it('upright static on slope gets non-zero sink', () => {
    const sink = sinkOffsetForSlope(Math.PI / 6, 0.5, 0);
    expect(sink).toBeGreaterThan(0.2);
  });

  it('fully aligned prop has zero residual sink', () => {
    const slope = Math.PI / 5;
    expect(sinkOffsetForSlope(slope, 0.8, slope)).toBe(0);
  });

  it('halfWidth 0 means callers skip sink entirely', () => {
    expect(sinkOffsetForSlope(Math.PI / 4, 0, 0)).toBe(0);
  });
});
