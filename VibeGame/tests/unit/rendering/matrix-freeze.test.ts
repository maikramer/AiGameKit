import { describe, expect, test } from 'bun:test';
import * as THREE from 'three';
import {
  isSubtreeMatrixFrozen,
  setSubtreeMatrixFrozen,
} from '../../../src/plugins/rendering/matrix-freeze';

function buildSubtree(): {
  root: THREE.Object3D;
  child: THREE.Object3D;
  grandchild: THREE.Object3D;
} {
  const root = new THREE.Object3D();
  const child = new THREE.Object3D();
  const grandchild = new THREE.Object3D();
  child.add(grandchild);
  root.add(child);
  return { root, child, grandchild };
}

describe('setSubtreeMatrixFrozen', () => {
  test('clears matrixAutoUpdate on the whole subtree', () => {
    const { root, child, grandchild } = buildSubtree();

    setSubtreeMatrixFrozen(root, true);

    expect(root.matrixAutoUpdate).toBe(false);
    expect(child.matrixAutoUpdate).toBe(false);
    expect(grandchild.matrixAutoUpdate).toBe(false);
  });

  test('restores the original flag on thaw', () => {
    const { root, child, grandchild } = buildSubtree();
    // A node that was already manual before freezing must stay manual.
    grandchild.matrixAutoUpdate = false;

    setSubtreeMatrixFrozen(root, true);
    setSubtreeMatrixFrozen(root, false);

    expect(root.matrixAutoUpdate).toBe(true);
    expect(child.matrixAutoUpdate).toBe(true);
    expect(grandchild.matrixAutoUpdate).toBe(false);
  });

  test('freezing twice does not lose the original flag', () => {
    const { root, grandchild } = buildSubtree();
    grandchild.matrixAutoUpdate = false;

    setSubtreeMatrixFrozen(root, true);
    setSubtreeMatrixFrozen(root, true);
    setSubtreeMatrixFrozen(root, false);

    expect(grandchild.matrixAutoUpdate).toBe(false);
    expect(root.matrixAutoUpdate).toBe(true);
  });

  test('thawing without a prior freeze leaves flags untouched', () => {
    const { root, child } = buildSubtree();
    child.matrixAutoUpdate = false;

    setSubtreeMatrixFrozen(root, false);

    expect(root.matrixAutoUpdate).toBe(true);
    expect(child.matrixAutoUpdate).toBe(false);
  });

  test('a frozen subtree skips world-matrix recomposition', () => {
    const { root, child } = buildSubtree();
    root.updateMatrixWorld(true);

    setSubtreeMatrixFrozen(root, true);
    child.position.set(5, 0, 0);
    root.updateMatrixWorld();

    expect(child.matrixWorld.elements[12]).toBe(0);
  });

  test('thaw forces the pose written while frozen to land', () => {
    const { root, child } = buildSubtree();
    root.updateMatrixWorld(true);

    setSubtreeMatrixFrozen(root, true);
    child.position.set(5, 0, 0);
    root.updateMatrixWorld();
    setSubtreeMatrixFrozen(root, false);
    root.updateMatrixWorld();

    expect(child.matrixWorld.elements[12]).toBe(5);
  });

  test('isSubtreeMatrixFrozen tracks the root state', () => {
    const { root } = buildSubtree();

    expect(isSubtreeMatrixFrozen(root)).toBe(false);
    setSubtreeMatrixFrozen(root, true);
    expect(isSubtreeMatrixFrozen(root)).toBe(true);
    setSubtreeMatrixFrozen(root, false);
    expect(isSubtreeMatrixFrozen(root)).toBe(false);
  });

  test('the parent walk skips a frozen subtree entirely', () => {
    const parent = new THREE.Object3D();
    const { root, child } = buildSubtree();
    parent.add(root);
    parent.updateMatrixWorld(true);

    setSubtreeMatrixFrozen(root, true);
    // Moving the parent normally propagates into every descendant; a frozen
    // root is not visited at all, so the subtree keeps its stale world matrix.
    parent.position.set(9, 0, 0);
    parent.updateMatrixWorld();

    expect(root.matrixWorldAutoUpdate).toBe(false);
    expect(child.matrixWorld.elements[12]).toBe(0);
  });

  test('thaw resyncs the subtree the parent walk skipped', () => {
    const parent = new THREE.Object3D();
    const { root, child } = buildSubtree();
    parent.add(root);
    parent.updateMatrixWorld(true);

    setSubtreeMatrixFrozen(root, true);
    parent.position.set(9, 0, 0);
    parent.updateMatrixWorld();
    setSubtreeMatrixFrozen(root, false);

    expect(root.matrixWorldAutoUpdate).toBe(true);
    expect(child.matrixWorld.elements[12]).toBe(9);
  });

  test('a root that was already world-manual stays manual after thaw', () => {
    const { root } = buildSubtree();
    root.matrixWorldAutoUpdate = false;

    setSubtreeMatrixFrozen(root, true);
    setSubtreeMatrixFrozen(root, false);

    expect(root.matrixWorldAutoUpdate).toBe(false);
  });
});
