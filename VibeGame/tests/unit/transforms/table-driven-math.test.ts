import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import {
  Transform,
  WorldTransform,
  composeTransformMatrix,
  copyTransform,
  decomposeTransformMatrix,
  planarYawRadians,
  setTransformFacingXZ,
  setTransformIdentity,
  setTransformYawRadians,
  syncEulerFromQuaternion,
  syncQuaternionFromEuler,
} from 'vibegame/transforms';

const entity = 42;
const DEG = Math.PI / 180;

describe('transforms table-driven — planarYawRadians', () => {
  for (let deg = 0; deg < 360; deg += 3) {
    const rad = deg * DEG;
    const dx = Math.sin(rad);
    const dz = Math.cos(rad);
    it(`planarYawRadians deg=${deg}`, () => {
      const got = planarYawRadians(dx, dz);
      expect(Math.sin(got)).toBeCloseTo(Math.sin(rad), 4);
      expect(Math.cos(got)).toBeCloseTo(Math.cos(rad), 4);
    });
  }
});

describe('transforms table-driven — setTransformFacingXZ', () => {
  const dirs: Array<{ dx: number; dz: number; label: string }> = [
    { dx: 0, dz: 1, label: 'forward+Z' },
    { dx: 1, dz: 0, label: 'right+X' },
    { dx: 0, dz: -1, label: 'back-Z' },
    { dx: -1, dz: 0, label: 'left-X' },
    { dx: 1, dz: 1, label: 'diag+X+Z' },
  ];
  for (let deg = 0; deg < 360; deg += 9) {
    const rad = deg * DEG;
    const dx = Math.sin(rad);
    const dz = Math.cos(rad);
    dirs.push({ dx, dz, label: `deg${deg}` });
  }
  for (const { dx, dz, label } of dirs) {
    it(`setTransformFacingXZ ${label}`, () => {
      setTransformIdentity(Transform, entity);
      setTransformFacingXZ(Transform, entity, dx, dz);
      const yawRad = planarYawRadians(dx, dz);
      const eulerYRad = Transform.eulerY[entity] * DEG;
      expect(Math.sin(eulerYRad)).toBeCloseTo(Math.sin(yawRad), 3);
      expect(Math.cos(eulerYRad)).toBeCloseTo(Math.cos(yawRad), 3);
      const qlen =
        Transform.rotX[entity] ** 2 +
        Transform.rotY[entity] ** 2 +
        Transform.rotZ[entity] ** 2 +
        Transform.rotW[entity] ** 2;
      expect(qlen).toBeCloseTo(1, 4);
      expect(Transform.dirty[entity]).toBe(1);
    });
  }
});

describe('transforms table-driven — matrix compose/decompose', () => {
  const matrix = new THREE.Matrix4();
  const pos = new THREE.Vector3();
  const rot = new THREE.Quaternion();
  const scale = new THREE.Vector3();

  for (let i = 0; i < 100; i++) {
    const px = (i % 10) * 0.37 - 1.5;
    const py = ((i * 3) % 11) * 0.21;
    const pz = ((i * 7) % 13) * -0.29;
    const sx = 0.5 + (i % 5) * 0.1;
    const sy = 0.8 + (i % 4) * 0.05;
    const sz = 1.0 + (i % 3) * 0.2;
    const yawDeg = (i * 17) % 360;
    it(`compose/decompose roundtrip case ${i}`, () => {
      setTransformIdentity(Transform, entity);
      Transform.posX[entity] = px;
      Transform.posY[entity] = py;
      Transform.posZ[entity] = pz;
      Transform.scaleX[entity] = sx;
      Transform.scaleY[entity] = sy;
      Transform.scaleZ[entity] = sz;
      setTransformYawRadians(Transform, entity, yawDeg * DEG);

      composeTransformMatrix(Transform, entity, matrix, pos, rot, scale);
      setTransformIdentity(WorldTransform, entity);
      decomposeTransformMatrix(matrix, WorldTransform, entity, pos, rot, scale);

      expect(WorldTransform.posX[entity]).toBeCloseTo(px, 4);
      expect(WorldTransform.posY[entity]).toBeCloseTo(py, 4);
      expect(WorldTransform.posZ[entity]).toBeCloseTo(pz, 4);
      expect(WorldTransform.scaleX[entity]).toBeCloseTo(sx, 4);
      expect(WorldTransform.scaleY[entity]).toBeCloseTo(sy, 4);
      expect(WorldTransform.scaleZ[entity]).toBeCloseTo(sz, 4);
    });
  }
});

describe('transforms table-driven — copyTransform and euler sync', () => {
  beforeEach(() => {
    setTransformIdentity(Transform, entity);
    setTransformIdentity(WorldTransform, entity);
  });

  for (let i = 0; i < 50; i++) {
    it(`copyTransform preserves fields case ${i}`, () => {
      Transform.posX[entity] = i;
      Transform.posY[entity] = i * 0.5;
      Transform.posZ[entity] = -i * 0.25;
      Transform.scaleX[entity] = 1 + i * 0.01;
      setTransformYawRadians(Transform, entity, i * DEG);
      copyTransform(Transform, WorldTransform, entity);
      expect(WorldTransform.posX[entity]).toBe(i);
      expect(WorldTransform.eulerY[entity]).toBeCloseTo(
        Transform.eulerY[entity],
        4
      );
      expect(WorldTransform.rotW[entity]).toBeCloseTo(
        Transform.rotW[entity],
        4
      );
    });
  }

  for (let deg = 0; deg < 180; deg += 6) {
    it(`syncEulerFromQuaternion preserves quaternion degY=${deg}`, () => {
      setTransformYawRadians(Transform, entity, deg * DEG);
      const qx = Transform.rotX[entity];
      const qy = Transform.rotY[entity];
      const qz = Transform.rotZ[entity];
      const qw = Transform.rotW[entity];
      Transform.eulerX[entity] = 999;
      Transform.eulerY[entity] = 999;
      Transform.eulerZ[entity] = 999;
      syncEulerFromQuaternion(Transform, entity);
      expect(Transform.rotX[entity]).toBeCloseTo(qx, 4);
      expect(Transform.rotY[entity]).toBeCloseTo(qy, 4);
      expect(Transform.rotZ[entity]).toBeCloseTo(qz, 4);
      expect(Transform.rotW[entity]).toBeCloseTo(qw, 4);
      expect(Number.isFinite(Transform.eulerY[entity])).toBe(true);
    });
  }

  for (let deg = 0; deg < 180; deg += 6) {
    it(`syncQuaternionFromEuler degY=${deg}`, () => {
      Transform.eulerX[entity] = 0;
      Transform.eulerY[entity] = deg;
      Transform.eulerZ[entity] = 0;
      syncQuaternionFromEuler(Transform, entity);
      const len =
        Transform.rotX[entity] ** 2 +
        Transform.rotY[entity] ** 2 +
        Transform.rotZ[entity] ** 2 +
        Transform.rotW[entity] ** 2;
      expect(len).toBeCloseTo(1, 4);
    });
  }
});
