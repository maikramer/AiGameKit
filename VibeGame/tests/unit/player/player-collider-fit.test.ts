import { describe, expect, it } from 'bun:test';
import { ColliderShape } from 'vibegame/physics';
import { applyPlayerColliderFromAabb } from '../../../src/plugins/player/player-collider-fit';

describe('applyPlayerColliderFromAabb', () => {
  it('cápsula centrada no AABB com pés na origem', () => {
    const fit = applyPlayerColliderFromAabb({
      box: { min: { x: -0.5, y: 0, z: -0.5 }, max: { x: 0.5, y: 2, z: 0.5 } },
      yOffset: 0,
      margin: 0.02,
    });
    expect(fit.shape).toBe(ColliderShape.Capsule);
    expect(fit.radius).toBeCloseTo(0.52, 2);
    expect(fit.height).toBeCloseTo(1, 2);
    expect(fit.posOffsetX).toBe(0);
    expect(fit.posOffsetY).toBe(1);
    expect(fit.posOffsetZ).toBe(0);
  });

  it('yOffset desloca centro Y para alinhar pés à origem', () => {
    const fit = applyPlayerColliderFromAabb({
      box: { min: { x: 0, y: -0.2, z: 0 }, max: { x: 1, y: 1.8, z: 1 } },
      yOffset: 0.2,
      margin: 0,
    });
    expect(fit.posOffsetY).toBeCloseTo(1, 5);
  });
});
