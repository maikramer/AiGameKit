import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import {
  findTreeSplitParts,
  prepareTreeFallHalves,
} from '../../../src/plugins/destructible/fx';

function mesh(name: string, y: number, height: number): THREE.Mesh {
  const geo = new THREE.BoxGeometry(0.4, height, 0.4);
  const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial());
  m.name = name;
  m.position.y = y;
  return m;
}

describe('findTreeSplitParts', () => {
  it('finds Stump and Top children', () => {
    const root = new THREE.Group();
    root.add(mesh('Stump', 0.3, 0.6));
    root.add(mesh('Top', 1.5, 2.0));
    const parts = findTreeSplitParts(root);
    expect(parts).not.toBeNull();
    expect(parts!.stump.name).toBe('Stump');
    expect(parts!.top.name).toBe('Top');
  });

  it('accepts canopy alias for top', () => {
    const root = new THREE.Group();
    root.add(mesh('tree_stump', 0.3, 0.6));
    root.add(mesh('Canopy', 1.5, 2.0));
    const parts = findTreeSplitParts(root);
    expect(parts).not.toBeNull();
    expect(parts!.top.name).toBe('Canopy');
  });

  it('returns null without both halves', () => {
    const root = new THREE.Group();
    root.add(mesh('Trunk', 1, 2));
    expect(findTreeSplitParts(root)).toBeNull();
  });
});

describe('prepareTreeFallHalves', () => {
  it('uses mesh path without clipping planes when Stump/Top exist', () => {
    const root = new THREE.Group();
    root.add(mesh('Stump', 0.3, 0.6));
    root.add(mesh('Top', 1.5, 2.0));
    root.updateMatrixWorld(true);

    const halves = prepareTreeFallHalves(root, 0.6);
    expect(halves).not.toBeNull();
    expect(halves!.topPlane).toBeNull();
    for (const mat of halves!.materials) {
      expect(mat.clippingPlanes == null || mat.clippingPlanes.length === 0).toBe(
        true
      );
    }
  });

  it('falls back to clipping planes for single-mesh trees', () => {
    const root = new THREE.Group();
    root.add(mesh('Tree', 1.0, 2.0));
    root.updateMatrixWorld(true);

    const halves = prepareTreeFallHalves(root, 0.6);
    expect(halves).not.toBeNull();
    expect(halves!.topPlane).not.toBeNull();
    const withClip = halves!.materials.filter(
      (m) => m.clippingPlanes && m.clippingPlanes.length > 0
    );
    expect(withClip.length).toBeGreaterThan(0);
  });
});
