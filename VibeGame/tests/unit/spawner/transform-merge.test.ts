import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { eulerToQuaternion } from 'vibegame';
import { composeSpawnRotation } from '../../../src/plugins/spawner/transform-merge';

function localUpAfterEuler(euler: { x: number; y: number; z: number }): THREE.Vector3 {
  const q = eulerToQuaternion(euler.x, euler.y, euler.z);
  const quat = new THREE.Quaternion(q.x, q.y, q.z, q.w);
  return new THREE.Vector3(0, 1, 0).applyQuaternion(quat);
}

describe('composeSpawnRotation', () => {
  it('com align ao terreno, +Y local permanece paralelo ao normal (yaw em torno do tronco)', () => {
    const n = new THREE.Vector3(0, Math.SQRT1_2, Math.SQRT1_2).normalize();
    const euler = composeSpawnRotation(n, true, 2.718, [0, 0, 0]);
    const up = localUpAfterEuler(euler);
    expect(Math.abs(up.dot(n))).toBeGreaterThan(1 - 1e-4);
  });

  it('sem align, yaw só em Y mundial', () => {
    const n = new THREE.Vector3(0, 1, 0);
    const euler = composeSpawnRotation(n, false, Math.PI / 4, [0, 0, 0]);
    const up = localUpAfterEuler(euler);
    expect(up.y).toBeGreaterThan(1 - 1e-4);
  });
});
