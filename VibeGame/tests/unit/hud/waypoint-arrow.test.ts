import { describe, expect, it } from 'bun:test';

import {
  DEFAULT_ARROW_MARGIN,
  computeArrowPlacement,
} from '../../../src/plugins/hud/widgets/waypoint-arrow';

const W = 800;
const H = 600;

describe('computeArrowPlacement', () => {
  it('places an on-screen target at its projected pixel', () => {
    const p = computeArrowPlacement({ x: 0, y: 0, z: 0.5 }, W, H);
    expect(p.onScreen).toBe(true);
    expect(p.x).toBeCloseTo(400, 5);
    expect(p.y).toBeCloseTo(300, 5);
    expect(p.angle).toBe(0);
  });

  it('maps NDC to screen with y flipped', () => {
    const p = computeArrowPlacement({ x: 0.5, y: 0.5, z: 0 }, W, H);
    expect(p.onScreen).toBe(true);
    expect(p.x).toBeCloseTo(600, 5);
    // +Y in NDC is up, which is a *smaller* CSS pixel y.
    expect(p.y).toBeCloseTo(150, 5);
  });

  it('clamps an off-screen target inside the margin', () => {
    const p = computeArrowPlacement({ x: 4, y: 0, z: 0.5 }, W, H, 50);
    expect(p.onScreen).toBe(false);
    expect(p.x).toBeCloseTo(W / 2 + (W / 2 - 50), 5);
    expect(p.y).toBeCloseTo(H / 2, 5);
  });

  it('points right for a target off the right edge', () => {
    const p = computeArrowPlacement({ x: 4, y: 0, z: 0.5 }, W, H);
    // angle 0 = up, so +π/2 = right.
    expect(p.angle).toBeCloseTo(Math.PI / 2, 5);
  });

  it('points up for a target off the top edge', () => {
    const p = computeArrowPlacement({ x: 0, y: 4, z: 0.5 }, W, H);
    expect(p.angle).toBeCloseTo(0, 5);
  });

  it('points down for a target off the bottom edge', () => {
    const p = computeArrowPlacement({ x: 0, y: -4, z: 0.5 }, W, H);
    expect(Math.abs(p.angle)).toBeCloseTo(Math.PI, 5);
  });

  it('flips a target behind the camera to the correct side', () => {
    // z > 1 means behind: three.js divides by a negative w, mirroring x/y.
    // Raw NDC x is negative, but the target is really to the player's right.
    const behind = computeArrowPlacement({ x: -0.4, y: 0, z: 2 }, W, H);
    expect(behind.onScreen).toBe(false);
    expect(behind.x).toBeGreaterThan(W / 2);
    expect(behind.angle).toBeGreaterThan(0);
  });

  it('still yields a direction for a target dead behind the camera', () => {
    const p = computeArrowPlacement({ x: 0, y: 0, z: 2 }, W, H);
    expect(p.onScreen).toBe(false);
    expect(Number.isFinite(p.x)).toBe(true);
    expect(Number.isFinite(p.y)).toBe(true);
    expect(Number.isFinite(p.angle)).toBe(true);
  });

  it('never clamps outside the viewport, even with a huge margin', () => {
    const p = computeArrowPlacement({ x: 9, y: 9, z: 0.5 }, W, H, 10_000);
    expect(p.x).toBeGreaterThanOrEqual(0);
    expect(p.x).toBeLessThanOrEqual(W);
    expect(p.y).toBeGreaterThanOrEqual(0);
    expect(p.y).toBeLessThanOrEqual(H);
  });

  it('defaults the margin when none is passed', () => {
    const explicit = computeArrowPlacement(
      { x: 4, y: 0, z: 0.5 },
      W,
      H,
      DEFAULT_ARROW_MARGIN
    );
    const implicit = computeArrowPlacement({ x: 4, y: 0, z: 0.5 }, W, H);
    expect(implicit.x).toBeCloseTo(explicit.x, 5);
  });

  it('treats the NDC unit square edge as on-screen', () => {
    expect(computeArrowPlacement({ x: 1, y: 1, z: 0 }, W, H).onScreen).toBe(
      true
    );
    expect(
      computeArrowPlacement({ x: 1.0001, y: 0, z: 0 }, W, H).onScreen
    ).toBe(false);
  });
});
