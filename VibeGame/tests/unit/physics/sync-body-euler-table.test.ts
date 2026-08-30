import { describe, expect, it } from 'bun:test';
import { Rigidbody } from 'aigamekit-vibegame/physics';
import {
  syncBodyEulerFromQuaternion,
  syncBodyQuaternionFromEuler,
} from '../../../src/plugins/physics/utils';

const entity = 11;

describe('physics syncBodyEuler/Quaternion table-driven', () => {
  for (let i = 0; i < 100; i++) {
    const yaw = (i * 13) % 360;
    it(`euler→quat→euler→quat stable case ${i} y=${yaw}`, () => {
      Rigidbody.eulerX[entity] = 0;
      Rigidbody.eulerY[entity] = yaw;
      Rigidbody.eulerZ[entity] = 0;
      syncBodyQuaternionFromEuler(entity);
      const qx0 = Rigidbody.rotX[entity];
      const qy0 = Rigidbody.rotY[entity];
      const qz0 = Rigidbody.rotZ[entity];
      const qw0 = Rigidbody.rotW[entity];
      syncBodyEulerFromQuaternion(entity);
      syncBodyQuaternionFromEuler(entity);
      const dot =
        qx0 * Rigidbody.rotX[entity] +
        qy0 * Rigidbody.rotY[entity] +
        qz0 * Rigidbody.rotZ[entity] +
        qw0 * Rigidbody.rotW[entity];
      expect(Math.abs(dot)).toBeCloseTo(1, 3);
    });
  }
});
