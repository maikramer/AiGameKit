import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { State } from 'vibegame';
import {
  clearGltfBoundsCache,
  getGltfLocalYBounds,
  normalizeGltfUrlKey,
  registerGltfLocalYBounds,
} from '../../../src/plugins/gltf-xml/gltf-bounds-cache';
import {
  getInstancePoolStats,
  getInstancedLodUrls,
  isGltfInstanced,
  markGltfInstanced,
  setInstancedLodThreshold,
  setInstancedLodUrl,
} from '../../../src/plugins/gltf-xml/auto-instance';
import {
  GltfLod,
  GltfPending,
  GltfPhysicsPending,
} from '../../../src/plugins/gltf-xml/components';

beforeEach(() => {
  clearGltfBoundsCache();
});

describe('normalizeGltfUrlKey', () => {
  for (const [raw, want] of [
    [' /a.glb ', '/a.glb'],
    ['x.glb', 'x.glb'],
    ['  trimmed  ', 'trimmed'],
  ] as const) {
    it(`"${raw}" → "${want}"`, () => {
      expect(normalizeGltfUrlKey(raw)).toBe(want);
    });
  }
});

describe('registerGltfLocalYBounds + getters', () => {
  for (const minY of [-1, 0, 0.2]) {
    for (const maxY of [0.5, 1, 2.5]) {
      it(`minY=${minY} maxY=${maxY}`, () => {
        const root = new THREE.Group();
        const geo = new THREE.BoxGeometry(1, maxY - minY, 1);
        geo.translate(0, (minY + maxY) / 2, 0);
        root.add(new THREE.Mesh(geo));
        const url = `/test/box-${minY}-${maxY}.glb`;
        registerGltfLocalYBounds(url, root);
        const y = getGltfLocalYBounds(url);
        expect(y).not.toBeNull();
        expect(y!.maxY - y!.minY).toBeCloseTo(maxY - minY, 2);
      });
    }
  }
});

describe('GltfPending / GltfPhysicsPending / GltfLod component arrays', () => {
  it('GltfPending.loaded defaults to 0 for new entity index', () => {
    expect(GltfPending.loaded[0]).toBe(0);
  });

  for (const field of ['ready', 'colliderShape', 'bodyType'] as const) {
    it(`GltfPhysicsPending.${field} default 0`, () => {
      expect(GltfPhysicsPending[field][42]).toBe(0);
    });
  }

  it('GltfLod.activeLevel default 0', () => {
    expect(GltfLod.activeLevel[7]).toBe(0);
  });
});

describe('instanced GLTF flags per State', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let eid = 1; eid <= 12; eid += 1) {
    it(`markGltfInstanced entity ${eid}`, () => {
      expect(isGltfInstanced(state, eid)).toBe(false);
      markGltfInstanced(state, eid);
      expect(isGltfInstanced(state, eid)).toBe(true);
    });
  }

  for (const level of [1, 2] as const) {
    it(`setInstancedLodUrl level ${level}`, () => {
      const eid = 50;
      setInstancedLodUrl(state, eid, level, `/lod${level}.glb`);
      const pair = getInstancedLodUrls(state, eid);
      expect(pair[level - 1]).toBe(`/lod${level}.glb`);
    });
  }

  it('setInstancedLodThreshold near/mid', () => {
    setInstancedLodThreshold(state, 9, 1, 22);
    setInstancedLodThreshold(state, 9, 2, 88);
    markGltfInstanced(state, 9);
    expect(getInstancePoolStats(state)).toEqual({
      poolCount: 0,
      slotCount: 0,
      pendingCount: 0,
    });
  });
});
