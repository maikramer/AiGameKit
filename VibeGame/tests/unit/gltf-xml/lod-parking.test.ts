import { describe, expect, test } from 'bun:test';
import * as THREE from 'three';
import {
  clearLodParking,
  forEachLodChild,
  getActiveLodLevel,
  getLodChild,
  hasLodChild,
  lodChildCount,
  registerLodChild,
  setActiveLodLevel,
} from '../../../src/extras/gltf-lod-parking';

function buildLodRoot(levels = [0, 1, 2]): {
  root: THREE.Object3D;
  children: THREE.Object3D[];
} {
  const root = new THREE.Object3D();
  root.name = 'gltf-lod-root';
  const children: THREE.Object3D[] = [];
  for (const level of levels) {
    const child = new THREE.Object3D();
    child.name = `lod${level}`;
    root.add(child);
    registerLodChild(root, child, level);
    children.push(child);
  }
  return { root, children };
}

describe('gltf lod parking', () => {
  test('keeps only the active level attached', () => {
    const { root, children } = buildLodRoot();

    expect(root.children).toEqual([children[0]]);
    expect(children[1].parent).toBeNull();
    expect(children[2].parent).toBeNull();
    expect(getActiveLodLevel(root)).toBe(0);
  });

  test('counts parked levels, unlike root.children', () => {
    const { root } = buildLodRoot();

    expect(lodChildCount(root)).toBe(3);
    expect(root.children.length).toBe(1);
  });

  test('switching level swaps which child is attached', () => {
    const { root, children } = buildLodRoot();

    setActiveLodLevel(root, 2);

    expect(root.children).toEqual([children[2]]);
    expect(children[0].parent).toBeNull();
    expect(children[2].visible).toBe(true);
    expect(getActiveLodLevel(root)).toBe(2);
  });

  test('re-attaching marks the world matrix stale', () => {
    const { root, children } = buildLodRoot();
    root.position.set(10, 0, 0);
    root.updateMatrixWorld(true);

    setActiveLodLevel(root, 1);
    root.updateMatrixWorld();

    expect(children[1].matrixWorld.elements[12]).toBe(10);
  });

  test('a level registered later is parked when it is not active', () => {
    const { root } = buildLodRoot([0]);
    const lod2 = new THREE.Object3D();
    root.add(lod2);

    registerLodChild(root, lod2, 2);

    expect(lod2.parent).toBeNull();
    expect(hasLodChild(root, 2)).toBe(true);
    expect(lodChildCount(root)).toBe(2);
  });

  test('an unknown level leaves the active one in place', () => {
    const { root, children } = buildLodRoot([0, 1]);

    setActiveLodLevel(root, 2);

    expect(root.children).toEqual([children[0]]);
    expect(getActiveLodLevel(root)).toBe(0);
  });

  test('getLodChild reaches parked levels', () => {
    const { root, children } = buildLodRoot();

    expect(getLodChild(root, 0)).toBe(children[0]);
    expect(getLodChild(root, 2)).toBe(children[2]);
    expect(getLodChild(root, 7)).toBeUndefined();
  });

  test('forEachLodChild visits attached and parked levels', () => {
    const { root } = buildLodRoot();
    const seen: number[] = [];

    forEachLodChild(root, (_child, level) => seen.push(level));

    expect(seen.sort()).toEqual([0, 1, 2]);
  });

  test('setActiveLodLevel is idempotent', () => {
    const { root, children } = buildLodRoot();

    setActiveLodLevel(root, 1);
    setActiveLodLevel(root, 1);

    expect(root.children).toEqual([children[1]]);
  });

  test('clearing the parking forgets the root', () => {
    const { root } = buildLodRoot();

    clearLodParking(root);

    expect(getActiveLodLevel(root)).toBe(-1);
    expect(hasLodChild(root, 0)).toBe(false);
    // Falls back to the live child list once the registry is gone.
    expect(lodChildCount(root)).toBe(root.children.length);
  });

  test('an unregistered root is left alone', () => {
    const root = new THREE.Object3D();
    const child = new THREE.Object3D();
    root.add(child);

    setActiveLodLevel(root, 1);

    expect(root.children).toEqual([child]);
    expect(lodChildCount(root)).toBe(1);
  });
});
