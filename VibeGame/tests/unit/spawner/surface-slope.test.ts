import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { isNormalWithinSlopeLimit, partialAlignEuler } from 'vibegame';

/** Apply the returned Euler the same way the vegetation instancer does
 * (Object3D.rotation defaults to XYZ order, radians) and return the trunk's
 * world-space up vector. */
function trunkUp(euler: [number, number, number]): THREE.Vector3 {
  const e = new THREE.Euler(euler[0], euler[1], euler[2], 'XYZ');
  return new THREE.Vector3(0, 1, 0).applyEuler(e);
}

describe('isNormalWithinSlopeLimit', () => {
  it('plano horizontal aceita até 45°', () => {
    const up = new THREE.Vector3(0, 1, 0);
    expect(isNormalWithinSlopeLimit(up, 45)).toBe(true);
  });

  it('normal a 45° da vertical está no limite', () => {
    const n = new THREE.Vector3(0, Math.SQRT1_2, Math.SQRT1_2).normalize();
    expect(isNormalWithinSlopeLimit(n, 45)).toBe(true);
  });

  it('normal mais íngreme que 45° é rejeitada', () => {
    const n = new THREE.Vector3(0, 0.5, Math.sqrt(0.75)).normalize();
    expect(isNormalWithinSlopeLimit(n, 45)).toBe(false);
  });

  it('90° aceita qualquer terreno com normal para cima', () => {
    const steep = new THREE.Vector3(0, 0.2, Math.sqrt(0.96)).normalize();
    expect(isNormalWithinSlopeLimit(steep, 90)).toBe(true);
  });
});

describe('partialAlignEuler', () => {
  it('terreno plano fica em pé, só com yaw (radianos)', () => {
    const up = new THREE.Vector3(0, 1, 0);
    const e = partialAlignEuler(up, Math.PI / 2, 0);
    expect(e).toEqual([0, Math.PI / 2, 0]);
    // trunk stays vertical
    const t = trunkUp(e);
    expect(t.y).toBeCloseTo(1, 6);
  });

  it('retorna radianos, não graus (lean suave, não tombado)', () => {
    // ~30° slope: normal tilted in +Z fall direction.
    const slopeRad = THREE.MathUtils.degToRad(30);
    const normal = new THREE.Vector3(
      0,
      Math.cos(slopeRad),
      Math.sin(slopeRad)
    ).normalize();
    const e = partialAlignEuler(normal, 0, slopeRad);
    // Magnitudes must be radian-sized (< ~0.3), never ~15+ (degrees-as-radians).
    expect(Math.abs(e[0])).toBeLessThan(0.3);
    expect(Math.abs(e[2])).toBeLessThan(0.3);
    // The trunk leans, but only gently — far from lying flat.
    const t = trunkUp(e);
    const lean = Math.acos(Math.min(1, Math.max(-1, t.y)));
    expect(lean).toBeGreaterThan(0.01);
    expect(lean).toBeLessThanOrEqual(0.26 + 1e-6);
  });

  it('encosta íngreme satura no tilt máximo (não tomba)', () => {
    const slopeRad = THREE.MathUtils.degToRad(70);
    const normal = new THREE.Vector3(
      0,
      Math.cos(slopeRad),
      Math.sin(slopeRad)
    ).normalize();
    const e = partialAlignEuler(normal, 0, slopeRad);
    const t = trunkUp(e);
    const lean = Math.acos(Math.min(1, Math.max(-1, t.y)));
    expect(lean).toBeCloseTo(0.26, 5);
  });

  it('inclina na direção da encosta (fall-line)', () => {
    const slopeRad = THREE.MathUtils.degToRad(40);
    // normal points up and toward +X → ground falls toward -X, trunk leans -X
    const normal = new THREE.Vector3(
      Math.sin(slopeRad),
      Math.cos(slopeRad),
      0
    ).normalize();
    const t = trunkUp(partialAlignEuler(normal, 0, slopeRad));
    // trunk should tip toward +X (same side as the normal's horizontal lean)
    expect(t.x).toBeGreaterThan(0.05);
    expect(Math.abs(t.z)).toBeLessThan(0.05);
  });
});
