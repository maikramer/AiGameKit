import * as THREE from 'three';
import { radToDeg } from '../../shared';
import { Transform, WorldTransform } from './components';

type TransformComponent = typeof Transform | typeof WorldTransform;

import { eulerToQuaternionInto, quaternionToEulerInto } from '../../core/math';

const _eulerScratch = { x: 0, y: 0, z: 0 };
const _quatScratch = { x: 0, y: 0, z: 0, w: 1 };

export function syncEulerFromQuaternion(
  transform: TransformComponent,
  entity: number
): void {
  quaternionToEulerInto(
    transform.rotX[entity],
    transform.rotY[entity],
    transform.rotZ[entity],
    transform.rotW[entity],
    _eulerScratch
  );
  transform.eulerX[entity] = _eulerScratch.x;
  transform.eulerY[entity] = _eulerScratch.y;
  transform.eulerZ[entity] = _eulerScratch.z;
}

export function syncQuaternionFromEuler(
  transform: TransformComponent,
  entity: number
): void {
  eulerToQuaternionInto(
    transform.eulerX[entity],
    transform.eulerY[entity],
    transform.eulerZ[entity],
    _quatScratch
  );
  transform.rotX[entity] = _quatScratch.x;
  transform.rotY[entity] = _quatScratch.y;
  transform.rotZ[entity] = _quatScratch.z;
  transform.rotW[entity] = _quatScratch.w;
}

/**
 * Planar yaw (radians) for a ground-plane direction.
 * Convention: ``atan2(dx, dz)`` — local +Z forward at yaw 0 (Three.js / Quaternius).
 */
export function planarYawRadians(dx: number, dz: number): number {
  return Math.atan2(dx, dz);
}

/**
 * Shortest signed angle from `from` to `to`, in radians — the wrapped
 * difference so steering never spins the long way around.
 */
export function shortestAngleDelta(from: number, to: number): number {
  return Math.atan2(Math.sin(to - from), Math.cos(to - from));
}

/** One slew step toward `target`, capped at `maxStep` radians. */
export function stepTowardYaw(
  current: number,
  target: number,
  maxStep: number
): number {
  const err = shortestAngleDelta(current, target);
  if (Math.abs(err) <= maxStep) return target;
  return current + Math.sign(err) * maxStep;
}

export function setTransformYawRadians(
  transform: TransformComponent,
  entity: number,
  yawRadians: number
): void {
  transform.eulerX[entity] = 0;
  transform.eulerY[entity] = radToDeg(yawRadians);
  transform.eulerZ[entity] = 0;
  syncQuaternionFromEuler(transform, entity);
  if ('dirty' in transform) {
    (transform as typeof Transform).dirty[entity] = 1;
  }
}

/** Face along a planar direction (optional yaw offset in radians for asset forward). */
export function setTransformFacingXZ(
  transform: TransformComponent,
  entity: number,
  dx: number,
  dz: number,
  yawOffsetRadians = 0
): void {
  setTransformYawRadians(
    transform,
    entity,
    planarYawRadians(dx, dz) + yawOffsetRadians
  );
}

export function copyTransform(
  from: TransformComponent,
  to: TransformComponent,
  entity: number
): void {
  to.posX[entity] = from.posX[entity];
  to.posY[entity] = from.posY[entity];
  to.posZ[entity] = from.posZ[entity];
  to.rotX[entity] = from.rotX[entity];
  to.rotY[entity] = from.rotY[entity];
  to.rotZ[entity] = from.rotZ[entity];
  to.rotW[entity] = from.rotW[entity];
  to.eulerX[entity] = from.eulerX[entity];
  to.eulerY[entity] = from.eulerY[entity];
  to.eulerZ[entity] = from.eulerZ[entity];
  to.scaleX[entity] = from.scaleX[entity];
  to.scaleY[entity] = from.scaleY[entity];
  to.scaleZ[entity] = from.scaleZ[entity];
}

export function setTransformIdentity(
  transform: TransformComponent,
  entity: number
): void {
  transform.posX[entity] = 0;
  transform.posY[entity] = 0;
  transform.posZ[entity] = 0;
  transform.rotX[entity] = 0;
  transform.rotY[entity] = 0;
  transform.rotZ[entity] = 0;
  transform.rotW[entity] = 1;
  transform.eulerX[entity] = 0;
  transform.eulerY[entity] = 0;
  transform.eulerZ[entity] = 0;
  transform.scaleX[entity] = 1;
  transform.scaleY[entity] = 1;
  transform.scaleZ[entity] = 1;
}

export function composeTransformMatrix(
  transform: TransformComponent,
  entity: number,
  matrix: THREE.Matrix4,
  position: THREE.Vector3,
  rotation: THREE.Quaternion,
  scale: THREE.Vector3
): void {
  position.set(
    transform.posX[entity],
    transform.posY[entity],
    transform.posZ[entity]
  );
  rotation.set(
    transform.rotX[entity],
    transform.rotY[entity],
    transform.rotZ[entity],
    transform.rotW[entity]
  );
  scale.set(
    transform.scaleX[entity],
    transform.scaleY[entity],
    transform.scaleZ[entity]
  );
  matrix.compose(position, rotation, scale);
}

export function decomposeTransformMatrix(
  matrix: THREE.Matrix4,
  transform: TransformComponent,
  entity: number,
  position: THREE.Vector3,
  rotation: THREE.Quaternion,
  scale: THREE.Vector3
): void {
  matrix.decompose(position, rotation, scale);
  transform.posX[entity] = position.x;
  transform.posY[entity] = position.y;
  transform.posZ[entity] = position.z;
  transform.rotX[entity] = rotation.x;
  transform.rotY[entity] = rotation.y;
  transform.rotZ[entity] = rotation.z;
  transform.rotW[entity] = rotation.w;
  syncEulerFromQuaternion(transform, entity);
  transform.scaleX[entity] = scale.x;
  transform.scaleY[entity] = scale.y;
  transform.scaleZ[entity] = scale.z;
}
