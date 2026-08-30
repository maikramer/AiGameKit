import { describe, expect, it } from 'bun:test';
import {
  addCameraShake,
  cameraShakeSample,
  getCameraShakeTrauma,
  resetCameraShake,
  tickCameraShake,
} from 'vibegame';

describe('camera shake — trauma model', () => {
  it('accumulates trauma and clamps at 1', () => {
    resetCameraShake();
    addCameraShake(0.4);
    addCameraShake(0.4);
    expect(getCameraShakeTrauma()).toBeCloseTo(0.8, 5);
    addCameraShake(0.9);
    expect(getCameraShakeTrauma()).toBe(1);
  });

  it('ignores non-positive amounts', () => {
    resetCameraShake();
    addCameraShake(-1);
    addCameraShake(0);
    expect(getCameraShakeTrauma()).toBe(0);
  });

  it('decays linearly with unscaled time and reaches exactly zero', () => {
    resetCameraShake();
    addCameraShake(0.7);
    tickCameraShake(0.25); // decay 1.4/s → 0.35 gone
    expect(getCameraShakeTrauma()).toBeCloseTo(0.35, 5);
    tickCameraShake(0.5);
    expect(getCameraShakeTrauma()).toBe(0);
  });

  it('samples zero offset when calm', () => {
    resetCameraShake();
    const s = cameraShakeSample(1.234);
    expect(s.x).toBe(0);
    expect(s.y).toBe(0);
    expect(s.z).toBe(0);
    expect(s.roll).toBe(0);
  });

  it('amplitude scales with trauma² (quadratic curve)', () => {
    resetCameraShake();
    addCameraShake(1);
    const loud = cameraShakeSample(0.1);
    const loudMag = Math.abs(loud.x) + Math.abs(loud.y);
    resetCameraShake();
    addCameraShake(0.5);
    const quiet = cameraShakeSample(0.1);
    const quietMag = Math.abs(quiet.x) + Math.abs(quiet.y);
    // Quarter trauma² (0.25) — with the same phase, magnitudes shrink to ~¼.
    expect(quietMag).toBeGreaterThan(0);
    expect(quietMag).toBeLessThan(loudMag * 0.3 + 1e-9);
  });

  it('offset stays within the documented bounds', () => {
    resetCameraShake();
    addCameraShake(1);
    for (let i = 0; i < 40; i++) {
      const s = cameraShakeSample(i * 0.041);
      expect(Math.abs(s.x)).toBeLessThanOrEqual(0.22 + 1e-9);
      expect(Math.abs(s.y)).toBeLessThanOrEqual(0.22 + 1e-9);
      expect(Math.abs(s.z)).toBeLessThanOrEqual(0.22 + 1e-9);
      expect(Math.abs(s.roll)).toBeLessThanOrEqual(0.03 + 1e-9);
    }
  });
});
