import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import {
  attachInstancedLodGeometry,
  type InstancedLodTarget,
} from '../../../src/plugins/gltf-xml/auto-instance';

function fakePool(castShadow: boolean): InstancedLodTarget & {
  lod: Array<[THREE.BufferGeometry, number]>;
  shadowLod: Array<[THREE.BufferGeometry, number]>;
} {
  return {
    castShadow,
    lod: [],
    shadowLod: [],
    addLOD(geometry, _material, distance) {
      this.lod.push([geometry as THREE.BufferGeometry, distance]);
    },
    addShadowLOD(geometry, distance) {
      this.shadowLod.push([geometry, distance]);
    },
  };
}

describe('attachInstancedLodGeometry', () => {
  const geometry = new THREE.BoxGeometry();
  const material = new THREE.MeshStandardMaterial();

  it('registers the level for the camera and for the shadow map', () => {
    const pool = fakePool(true);

    attachInstancedLodGeometry(pool, geometry, material, 40);

    expect(pool.lod).toEqual([[geometry, 40]]);
    expect(pool.shadowLod).toEqual([[geometry, 40]]);
  });

  it('leaves a shadowless pool shadowless', () => {
    // addShadowLOD force-sets castShadow on the pool — calling it here would
    // hand shadows to props the author deliberately left without one.
    const pool = fakePool(false);

    attachInstancedLodGeometry(pool, geometry, material, 40);

    expect(pool.lod.length).toBe(1);
    expect(pool.shadowLod.length).toBe(0);
    expect(pool.castShadow).toBe(false);
  });

  it('keeps the camera and shadow ladders at matching distances', () => {
    const pool = fakePool(true);
    const lod2 = new THREE.BoxGeometry(2, 2, 2);

    attachInstancedLodGeometry(pool, geometry, material, 40);
    attachInstancedLodGeometry(pool, lod2, material, 110);

    expect(pool.lod.map(([, d]) => d)).toEqual([40, 110]);
    expect(pool.shadowLod.map(([, d]) => d)).toEqual([40, 110]);
    expect(pool.shadowLod[1]![0]).toBe(lod2);
  });
});
