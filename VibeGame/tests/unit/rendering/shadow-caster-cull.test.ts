import { describe, expect, test } from 'bun:test';
import * as THREE from 'three';
import {
  cullShadowCastersInSubtree,
  isShadowCasterCulled,
  MIN_CULL_DISTANCE,
  SHADOW_CULL_HYSTERESIS,
  restoreCulledShadowCasters,
  shouldKeepShadow,
} from '../../../src/plugins/rendering/shadow-caster-cull';

describe('shouldKeepShadow', () => {
  test('keeps every caster when the cull is disabled', () => {
    expect(shouldKeepShadow(0.05, 500, 0, false)).toBe(true);
  });

  test('keeps casters inside the always-on radius', () => {
    // A pebble at the player's feet still casts: near shadows are the ones
    // players actually read.
    expect(shouldKeepShadow(0.02, MIN_CULL_DISTANCE - 1, 0.01, false)).toBe(
      true
    );
  });

  test('drops a caster too small to resolve at its distance', () => {
    // 0.5m radius at 100m = 0.005 angular — half the 0.01 threshold.
    expect(shouldKeepShadow(0.5, 100, 0.01, false)).toBe(false);
  });

  test('keeps a large caster at the same distance', () => {
    // 4m boulder at 100m = 0.04 angular.
    expect(shouldKeepShadow(4, 100, 0.01, false)).toBe(true);
  });

  test('is size-relative, not distance-relative', () => {
    const near = shouldKeepShadow(1, 60, 0.01, false);
    const far = shouldKeepShadow(1, 200, 0.01, false);
    expect(near).toBe(true);
    expect(far).toBe(false);
  });

  test('hysteresis makes a culled caster earn its shadow back', () => {
    const ratio = 0.01;
    // Exactly at the threshold: fresh caster keeps it, culled one does not.
    const radius = ratio * 100;
    expect(shouldKeepShadow(radius, 100, ratio, false)).toBe(true);
    expect(shouldKeepShadow(radius, 100, ratio, true)).toBe(false);
    // Past the hysteresis band it comes back.
    const bigger = ratio * SHADOW_CULL_HYSTERESIS * 100;
    expect(shouldKeepShadow(bigger, 100, ratio, true)).toBe(true);
  });

  test('treats a degenerate radius as "keep" rather than culling blind', () => {
    expect(shouldKeepShadow(0, 500, 0.01, false)).toBe(true);
  });
});

describe('restoreCulledShadowCasters', () => {
  test('is a no-op for casters this pass never touched', () => {
    const root = new THREE.Object3D();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry());
    mesh.castShadow = true;
    root.add(mesh);

    restoreCulledShadowCasters(root);

    expect(mesh.castShadow).toBe(true);
  });
});

describe('cullShadowCastersInSubtree', () => {
  const cam = new THREE.Vector3(0, 0, 0);

  function propAt(distance: number, radius: number): THREE.Mesh {
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 6, 4));
    mesh.castShadow = true;
    mesh.position.set(0, 0, distance);
    mesh.updateMatrixWorld(true);
    return mesh;
  }

  test('drops the shadow of a small far prop and restores it when it nears', () => {
    const root = new THREE.Object3D();
    const prop = propAt(200, 0.5); // 0.0025 angular
    root.add(prop);

    cullShadowCastersInSubtree(root, cam, 0.01);
    expect(prop.castShadow).toBe(false);
    expect(isShadowCasterCulled(prop)).toBe(true);

    prop.position.set(0, 0, 30); // 0.017 angular — past the hysteresis band
    prop.updateMatrixWorld(true);
    cullShadowCastersInSubtree(root, cam, 0.01);

    expect(prop.castShadow).toBe(true);
    expect(isShadowCasterCulled(prop)).toBe(false);
  });

  test('leaves a big far prop casting', () => {
    const root = new THREE.Object3D();
    const cliff = propAt(200, 12);
    root.add(cliff);

    cullShadowCastersInSubtree(root, cam, 0.01);

    expect(cliff.castShadow).toBe(true);
  });

  test('never touches a caster that was already off', () => {
    const root = new THREE.Object3D();
    const prop = propAt(200, 0.5);
    prop.castShadow = false;
    root.add(prop);

    cullShadowCastersInSubtree(root, cam, 0.01);
    // Not ours to restore: an author-disabled caster must stay disabled.
    expect(isShadowCasterCulled(prop)).toBe(false);
    expect(prop.castShadow).toBe(false);
  });

  test('skips instanced pools — one flag covers every instance', () => {
    const root = new THREE.Object3D();
    const pool = new THREE.InstancedMesh(
      new THREE.SphereGeometry(0.5, 6, 4),
      new THREE.MeshBasicMaterial(),
      4
    );
    pool.castShadow = true;
    pool.position.set(0, 0, 400);
    pool.updateMatrixWorld(true);
    root.add(pool);

    cullShadowCastersInSubtree(root, cam, 0.01);

    expect(pool.castShadow).toBe(true);
  });

  test('does not descend into hidden subtrees (DistanceCull owns those)', () => {
    const root = new THREE.Object3D();
    const hidden = new THREE.Object3D();
    hidden.visible = false;
    const prop = propAt(300, 0.4);
    hidden.add(prop);
    root.add(hidden);

    cullShadowCastersInSubtree(root, cam, 0.01);

    expect(prop.castShadow).toBe(true);
    expect(isShadowCasterCulled(prop)).toBe(false);
  });

  test('scales the radius with the object — a scaled-up prop keeps casting', () => {
    const root = new THREE.Object3D();
    const prop = propAt(200, 0.5);
    prop.scale.setScalar(10); // 5m radius at 200m = 0.025 angular
    prop.updateMatrixWorld(true);
    root.add(prop);

    cullShadowCastersInSubtree(root, cam, 0.01);

    expect(prop.castShadow).toBe(true);
  });

  test('restoreCulledShadowCasters undoes a pass wholesale', () => {
    const root = new THREE.Object3D();
    const prop = propAt(200, 0.5);
    root.add(prop);

    cullShadowCastersInSubtree(root, cam, 0.01);
    expect(prop.castShadow).toBe(false);

    restoreCulledShadowCasters(root);

    expect(prop.castShadow).toBe(true);
    expect(isShadowCasterCulled(prop)).toBe(false);
  });
});
