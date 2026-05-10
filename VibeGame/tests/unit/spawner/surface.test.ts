import { describe, expect, it } from 'bun:test';
import { normalFromHeightSampler } from 'vibegame';

describe('normalFromHeightSampler', () => {
  it('plano inclinado h = 0.1*x + 0.2*z', () => {
    const heightAt = (x: number, z: number) => 0.1 * x + 0.2 * z;
    const n = normalFromHeightSampler(heightAt, 3, -2, 0.5);
    expect(n.x).toBeCloseTo(-0.1 / Math.sqrt(0.01 + 1 + 0.04), 5);
    expect(n.y).toBeCloseTo(1 / Math.sqrt(1.05), 5);
    expect(n.z).toBeCloseTo(-0.2 / Math.sqrt(1.05), 5);
  });

  it('plano horizontal retorna ~+Y', () => {
    const heightAt = () => 5;
    const n = normalFromHeightSampler(heightAt, 0, 0, 1);
    expect(n.x).toBeCloseTo(0, 5);
    expect(n.y).toBeCloseTo(1, 5);
    expect(n.z).toBeCloseTo(0, 5);
  });
});
