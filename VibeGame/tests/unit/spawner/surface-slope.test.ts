import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { isNormalWithinSlopeLimit } from 'vibegame';

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
