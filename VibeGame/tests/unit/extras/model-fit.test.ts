import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { fitModel, measureModelAxis } from '../../../src/extras/model-fit';

/**
 * Build a box-shaped "model" of the given size, optionally with a tapered nose
 * at +Z so the front is narrower than the back (which is how the axis measure
 * resolves the 180° ambiguity).
 *
 * Note the tests build geometry in the orientation they want to test rather
 * than rotating the root: `measureModelAxis` reports the axis in the object's
 * own local frame, precisely so `fitModel` can add a correction to that root's
 * rotation without chasing its own tail.
 */
function makeModel(
  sizeX: number,
  sizeY: number,
  sizeZ: number,
  options: { nose?: boolean } = {}
): THREE.Object3D {
  const root = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(sizeX, sizeY, sizeZ * 0.7));
  body.position.z = -sizeZ * 0.15;
  root.add(body);
  if (options.nose) {
    const nose = new THREE.Mesh(
      new THREE.BoxGeometry(sizeX * 0.35, sizeY * 0.6, sizeZ * 0.3)
    );
    nose.position.z = sizeZ * 0.35;
    root.add(nose);
  } else {
    const tail = new THREE.Mesh(
      new THREE.BoxGeometry(sizeX, sizeY, sizeZ * 0.3)
    );
    tail.position.z = sizeZ * 0.35;
    root.add(tail);
  }
  return root;
}

/** The same shape laid out along X instead of Z. */
function makeModelAlongX(
  sizeX: number,
  sizeY: number,
  sizeZ: number
): THREE.Object3D {
  const root = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(sizeX * 0.7, sizeY, sizeZ));
  body.position.x = -sizeX * 0.15;
  const nose = new THREE.Mesh(
    new THREE.BoxGeometry(sizeX * 0.3, sizeY * 0.6, sizeZ * 0.35)
  );
  nose.position.x = sizeX * 0.35;
  root.add(body, nose);
  return root;
}

/** Nose-at-minus-Z variant, for the front/back ambiguity test. */
function makeModelNoseAtMinusZ(
  sizeX: number,
  sizeY: number,
  sizeZ: number
): THREE.Object3D {
  const root = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(sizeX, sizeY, sizeZ * 0.7));
  body.position.z = sizeZ * 0.15;
  const nose = new THREE.Mesh(
    new THREE.BoxGeometry(sizeX * 0.35, sizeY * 0.6, sizeZ * 0.3)
  );
  nose.position.z = -sizeZ * 0.35;
  root.add(body, nose);
  return root;
}

function worldSize(object: THREE.Object3D): THREE.Vector3 {
  object.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(object).getSize(new THREE.Vector3());
}

describe('measureModelAxis', () => {
  it('finds a long axis already aligned with +Z', () => {
    const axis = measureModelAxis(makeModel(2, 1, 6, { nose: true }));
    expect(Math.abs(axis.angle)).toBeLessThan(0.1);
    expect(axis.elongation).toBeGreaterThan(2);
  });

  it('finds a long axis running along X', () => {
    const model = makeModelAlongX(6, 1, 2);
    const axis = measureModelAxis(model);
    expect(Math.abs(Math.abs(axis.angle) - Math.PI / 2)).toBeLessThan(0.1);
    expect(axis.elongation).toBeGreaterThan(2);
  });

  it('takes the narrow end as the front', () => {
    // Nose at +Z → the axis points +Z (angle ≈ 0).
    const forward = makeModel(2, 1, 6, { nose: true });
    expect(Math.abs(measureModelAxis(forward).angle)).toBeLessThan(0.1);
    // The same body with its nose at -Z → the axis points -Z (angle ≈ ±π).
    const reversed = makeModelNoseAtMinusZ(2, 1, 6);
    expect(
      Math.abs(Math.abs(measureModelAxis(reversed).angle) - Math.PI)
    ).toBeLessThan(0.2);
  });

  it('reports a low elongation for something square in plan', () => {
    const axis = measureModelAxis(makeModel(3, 1, 3));
    expect(axis.elongation).toBeLessThan(1.3);
  });

  it('survives a model with no geometry', () => {
    const axis = measureModelAxis(new THREE.Group());
    expect(axis.angle).toBe(0);
    expect(axis.elongation).toBe(1);
  });
});

describe('fitModel', () => {
  it('scales to a target height and seats the base on the ground', () => {
    const model = makeModel(2, 4, 2);
    model.position.set(17, 9, -4);
    fitModel(model, { fit: 'height', size: 8 });
    const size = worldSize(model);
    expect(size.y).toBeCloseTo(8, 1);
    const box = new THREE.Box3().setFromObject(model);
    expect(box.min.y).toBeCloseTo(0, 3);
    expect((box.min.x + box.max.x) / 2).toBeCloseTo(0, 3);
    expect((box.min.z + box.max.z) / 2).toBeCloseTo(0, 3);
  });

  it('scales to a target length along +Z', () => {
    const model = makeModel(2, 1.5, 6, { nose: true });
    fitModel(model, { fit: 'length', size: 3 });
    expect(worldSize(model).z).toBeCloseTo(3, 1);
  });

  it('turns a sideways model to face +Z', () => {
    const model = makeModelAlongX(6, 1.5, 2);
    fitModel(model, { align: 'forward' });
    const size = worldSize(model);
    expect(size.z).toBeGreaterThan(size.x * 2);
  });

  it('turns a model across the road when asked to', () => {
    const model = makeModel(2, 1.5, 6, { nose: true });
    fitModel(model, { align: 'across' });
    const size = worldSize(model);
    expect(size.x).toBeGreaterThan(size.z * 2);
  });

  it('stands up a model exported lying on its side', () => {
    // 9 m on Z, 2 m on X and Y — a tree on its back.
    const tree = makeModel(2, 2, 9);
    fitModel(tree, { fit: 'height', size: 9 });
    const size = worldSize(tree);
    expect(size.y).toBeCloseTo(9, 1);
    expect(size.z).toBeLessThan(4);
  });

  it('does not stand up a car just because it is longer than it is tall', () => {
    // A kart: 2.4 wide, 1.5 tall, 2.7 long. The naive rule flipped it onto its
    // nose; the strict one leaves it alone.
    const kart = makeModel(2.4, 1.5, 2.7);
    fitModel(kart, { fit: 'length', size: 2.7, minElongation: 1.6 });
    const size = worldSize(kart);
    expect(size.y).toBeLessThan(2);
    expect(size.z).toBeCloseTo(2.7, 1);
  });

  it('honours standUp: never', () => {
    const model = makeModel(2, 2, 9);
    fitModel(model, { standUp: 'never', minElongation: 99 });
    const size = worldSize(model);
    expect(size.z).toBeGreaterThan(size.y * 2);
  });

  it('applies an explicit yaw on top of the alignment', () => {
    const model = makeModel(2, 1.5, 6, { nose: true });
    fitModel(model, { align: 'forward', yawDegrees: 90, minElongation: 99 });
    const size = worldSize(model);
    expect(size.x).toBeGreaterThan(size.z * 2);
  });

  it('leaves a near-square model unrotated', () => {
    const model = makeModel(3, 1, 3.1);
    const before = model.rotation.y;
    fitModel(model, { minElongation: 1.5 });
    expect(model.rotation.y).toBe(before);
  });
});
