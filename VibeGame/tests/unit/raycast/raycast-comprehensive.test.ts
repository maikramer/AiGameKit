import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { RaycastHit, RaycastSource } from 'vibegame';
import { screenToWorldRay } from '../../../src/plugins/raycast/utils';

describe('raycast RaycastSource field table-driven', () => {
  for (let eid = 1; eid <= 20; eid++) {
    it(`RaycastSource direction write eid=${eid}`, () => {
      RaycastSource.dirX[eid] = eid * 0.1;
      RaycastSource.dirY[eid] = 0;
      RaycastSource.dirZ[eid] = 1;
      expect(RaycastSource.dirX[eid]).toBeCloseTo(eid * 0.1);
      expect(RaycastSource.dirZ[eid]).toBe(1);
    });
  }

  for (let eid = 1; eid <= 15; eid++) {
    it(`RaycastSource maxDist eid=${eid}`, () => {
      RaycastSource.maxDist[eid] = 10 + eid;
      expect(RaycastSource.maxDist[eid]).toBe(10 + eid);
    });
  }

  const masks = [
    0, 1, 0xff, 0xffff, 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0020,
  ];
  for (const mask of masks) {
    it(`RaycastSource layerMask 0x${mask.toString(16)}`, () => {
      RaycastSource.layerMask[5] = mask;
      expect(RaycastSource.layerMask[5]).toBe(mask);
    });
  }

  for (let mode = 0; mode <= 3; mode++) {
    for (let eid = 1; eid <= 5; eid++) {
      it(`RaycastSource mode=${mode} eid=${eid}`, () => {
        RaycastSource.mode[eid] = mode;
        expect(RaycastSource.mode[eid]).toBe(mode);
      });
    }
  }
});

describe('raycast RaycastHit field table-driven', () => {
  for (let eid = 1; eid <= 15; eid++) {
    it(`RaycastHit hitValid toggle eid=${eid}`, () => {
      RaycastHit.hitValid[eid] = 1;
      expect(RaycastHit.hitValid[eid]).toBe(1);
      RaycastHit.hitValid[eid] = 0;
      expect(RaycastHit.hitValid[eid]).toBe(0);
    });
  }

  for (let eid = 1; eid <= 15; eid++) {
    it(`RaycastHit hitEntity eid=${eid}`, () => {
      RaycastHit.hitEntity[eid] = 100 + eid;
      expect(RaycastHit.hitEntity[eid]).toBe(100 + eid);
    });
  }

  for (let dist of [0.1, 0.5, 1, 2, 5, 10, 25, 50, 100, 250]) {
    it(`RaycastHit hitDist ${dist}`, () => {
      RaycastHit.hitDist[3] = dist;
      expect(RaycastHit.hitDist[3]).toBeCloseTo(dist);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`RaycastHit normal vector n-${i}`, () => {
      RaycastHit.hitNormalX[7] = i * 0.1;
      RaycastHit.hitNormalY[7] = 1;
      RaycastHit.hitNormalZ[7] = 0;
      expect(RaycastHit.hitNormalY[7]).toBe(1);
    });
  }

  for (let i = 0; i < 10; i++) {
    it(`RaycastHit point p-${i}`, () => {
      RaycastHit.hitPointX[9] = i;
      RaycastHit.hitPointY[9] = i + 1;
      RaycastHit.hitPointZ[9] = i + 2;
      expect(RaycastHit.hitPointZ[9]).toBe(i + 2);
    });
  }
});

describe('raycast screenToWorldRay NDC table-driven', () => {
  const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 100);
  camera.position.set(0, 5, 10);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);

  const ndcPoints: Array<[number, number]> = [
    [0, 0],
    [-1, -1],
    [1, 1],
    [-1, 1],
    [1, -1],
    [0, 1],
    [0, -1],
    [1, 0],
    [-1, 0],
    [0.5, 0.5],
    [-0.5, -0.5],
    [0.25, -0.75],
  ];

  for (const [nx, ny] of ndcPoints) {
    it(`screenToWorldRay ndc (${nx},${ny}) normalized direction`, () => {
      const origin = new THREE.Vector3();
      const dir = new THREE.Vector3();
      screenToWorldRay(camera, nx, ny, origin, dir);
      expect(origin.x).toBeCloseTo(camera.position.x, 4);
      expect(origin.y).toBeCloseTo(camera.position.y, 4);
      expect(origin.z).toBeCloseTo(camera.position.z, 4);
      expect(dir.length()).toBeCloseTo(1, 4);
    });
  }

  for (let i = 0; i < 8; i++) {
    it(`screenToWorldRay center ray points forward-ish ${i}`, () => {
      const origin = new THREE.Vector3();
      const dir = new THREE.Vector3();
      screenToWorldRay(camera, 0, 0, origin, dir);
      expect(dir.y).toBeLessThan(0);
    });
  }
});

describe('raycast independent entities', () => {
  for (let a = 1; a <= 10; a++) {
    for (let b = 11; b <= 12; b++) {
      it(`sources ${a} vs ${b} independent`, () => {
        RaycastSource.maxDist[a] = a;
        RaycastSource.maxDist[b] = b;
        expect(RaycastSource.maxDist[a]).toBe(a);
        expect(RaycastSource.maxDist[b]).toBe(b);
      });
    }
  }
});
