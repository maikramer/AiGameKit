import { describe, expect, it } from 'bun:test';
import {
  DEFAULT_GRAVITY,
  integrateFallVelocity,
} from '../../../src/plugins/physics/utils';

const STEP = 1 / 60;

describe('integrateFallVelocity', () => {
  it('accumulates gravity while below terminal speed', () => {
    expect(integrateFallVelocity(0, DEFAULT_GRAVITY, STEP)).toBeCloseTo(-1, 5);
    expect(integrateFallVelocity(-10, DEFAULT_GRAVITY, STEP)).toBeCloseTo(
      -11,
      5
    );
  });

  it('caps a long fall at terminal speed', () => {
    let vy = 0;
    for (let i = 0; i < 60 * 30; i++) {
      vy = integrateFallVelocity(vy, DEFAULT_GRAVITY, STEP);
    }
    expect(vy).toBeCloseTo(DEFAULT_GRAVITY * 1.5, 5);
  });

  it('keeps the per-step drop small enough for the ground sweep', () => {
    let vy = 0;
    for (let i = 0; i < 60 * 30; i++) {
      vy = integrateFallVelocity(vy, DEFAULT_GRAVITY, STEP);
    }
    expect(Math.abs(vy * STEP)).toBeLessThan(2);
  });

  it('scales terminal speed with gravity scale', () => {
    let vy = 0;
    for (let i = 0; i < 600; i++) {
      vy = integrateFallVelocity(vy, DEFAULT_GRAVITY * 0.5, STEP);
    }
    expect(vy).toBeCloseTo(DEFAULT_GRAVITY * 0.5 * 1.5, 5);
  });

  it('leaves upward velocity untouched by the cap', () => {
    expect(integrateFallVelocity(20, DEFAULT_GRAVITY, STEP)).toBeCloseTo(19, 5);
  });

  it('is a no-op with zero gravity', () => {
    expect(integrateFallVelocity(-5, 0, STEP)).toBe(-5);
  });
});
