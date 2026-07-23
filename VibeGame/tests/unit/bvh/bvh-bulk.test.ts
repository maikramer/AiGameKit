import * as THREE from 'three';
import { beforeEach, describe, expect, it } from 'bun:test';
import {
  State,
  castBvhRay,
  getBvhContext,
  getBvhStats,
  getBvhSurfaceHeight,
  registerBvhMesh,
  unregisterBvhMesh,
} from 'vibegame';

function flatPlane(y: number, size: number): THREE.BufferGeometry {
  const h = size / 2;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute(
    'position',
    new THREE.BufferAttribute(
      new Float32Array([-h, y, -h, h, y, -h, -h, y, h, h, y, h]),
      3
    )
  );
  geo.setIndex(
    new THREE.BufferAttribute(new Uint32Array([0, 2, 1, 1, 2, 3]), 1)
  );
  return geo;
}

describe('bvh bulk: register keys', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 0; i < 25; i++) {
    it(`register mesh key plane-${i} increases meshCount`, () => {
      registerBvhMesh(state, `plane-${i}`, flatPlane(i * 0.1, 10));
      expect(getBvhStats(state).meshCount).toBe(1);
    });
  }
});

describe('bvh bulk: layer masks', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    registerBvhMesh(state, 'floor', flatPlane(0, 20), { layer: 0x0004 });
  });

  for (let mask = 0; mask < 16; mask++) {
    it(`raycast layerMask 0x${mask.toString(16)}`, () => {
      const origin = new THREE.Vector3(0, 5, 0);
      const dir = new THREE.Vector3(0, -1, 0);
      const hit = castBvhRay(state, origin, dir, 20, mask);
      const expectHit = (0x0004 & mask) !== 0;
      if (expectHit) {
        expect(hit).not.toBeNull();
        expect(hit!.layer).toBe(0x0004);
      } else {
        expect(hit).toBeNull();
      }
    });
  }
});

describe('bvh bulk: surface height grid', () => {
  for (let xi = -4; xi <= 4; xi++) {
    for (let zi = -4; zi <= 4; zi++) {
      it(`getBvhSurfaceHeight at (${xi}, ${zi})`, () => {
        const state = new State();
        registerBvhMesh(state, 'ground', flatPlane(2.5, 30));
        const y = getBvhSurfaceHeight(state, xi, 50, zi);
        expect(y).not.toBeNull();
        expect(y!).toBeCloseTo(2.5, 3);
      });
    }
  }
});

describe('bvh bulk: miss rays', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    registerBvhMesh(state, 'tri', flatPlane(0, 2));
  });

  for (let i = 0; i < 20; i++) {
    it(`ray miss upward ${i}`, () => {
      const origin = new THREE.Vector3(0, 0, 0);
      const dir = new THREE.Vector3(0, 1, 0);
      expect(castBvhRay(state, origin, dir, 10)).toBeNull();
    });
  }
});

describe('bvh bulk: unregister', () => {
  for (let i = 0; i < 15; i++) {
    it(`unregister plane-${i} drops count`, () => {
      const state = new State();
      const key = `plane-${i}`;
      registerBvhMesh(state, key, flatPlane(0, 4));
      unregisterBvhMesh(state, key);
      expect(getBvhContext(state).entries.has(key)).toBe(false);
      expect(getBvhStats(state).meshCount).toBe(0);
    });
  }
});

describe('bvh bulk: entity indexing', () => {
  for (let entity = 1; entity <= 20; entity++) {
    it(`entityKeys for entity ${entity}`, () => {
      const state = new State();
      registerBvhMesh(state, `e:${entity}`, flatPlane(0, 4), { entity });
      expect(getBvhContext(state).entityKeys.get(entity)).toEqual([
        `e:${entity}`,
      ]);
      expect(getBvhStats(state).entityCount).toBe(1);
    });
  }
});
