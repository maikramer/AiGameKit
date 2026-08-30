import * as THREE from 'three';
import { radToDeg } from '../../shared';
import { Transform, WorldTransform } from './components';

type TransformComponent = typeof Transform | typeof WorldTransform;

import { eulerToQuaternionInto, quaternionToEulerInto } from '../../core/math';
import { MAX_ENTITIES } from '../../core/ecs/constants';

const _eulerScratch = { x: 0, y: 0, z: 0 };
const _quatScratch = { x: 0, y: 0, z: 0, w: 1 };

/**
 * Which of a Transform's two rotation representations was written last.
 *
 * `Transform` carries both a quaternion and Euler angles, and different writers
 * use different ones: XML authoring and gameplay code set `eulerY`, physics and
 * animation set `rotX..rotW`. Whoever wrote last has to win, and comparing the
 * two against each other cannot tell you who that was — a mismatch looks
 * identical either way. So the resolved pair is remembered per entity and the
 * side that moved since is the author.
 *
 * Getting this wrong is not subtle: preferring the quaternion unconditionally
 * silently discards every `eulerY` an author writes (a `<Group rotation>` never
 * turns), and preferring the Euler unconditionally snaps physics-driven bodies
 * back to their last authored angle every frame.
 */
export type RotationSource = 'euler' | 'quaternion' | 'unchanged';

/**
 * Shadows are keyed by the owning `State`: component arrays are module-level,
 * so a fresh `State` reuses entity id 1 with a brand-new pose while the old
 * shadow still describes the previous world. Stamping the owner turns that into
 * "no history" instead of a wrong answer — which is exactly how a second scene
 * (or the next test in a file) used to lose its authored rotations.
 */
const stateIds = new WeakMap<object, number>();
let nextStateId = 1;

function stateId(state: object): number {
  let id = stateIds.get(state);
  if (id === undefined) {
    id = nextStateId++;
    stateIds.set(state, id);
  }
  return id;
}

const shadowOwner = new Uint32Array(MAX_ENTITIES);
const shadowEulerX = new Float32Array(MAX_ENTITIES);
const shadowEulerY = new Float32Array(MAX_ENTITIES);
const shadowEulerZ = new Float32Array(MAX_ENTITIES);
const shadowRotX = new Float32Array(MAX_ENTITIES);
const shadowRotY = new Float32Array(MAX_ENTITIES);
const shadowRotZ = new Float32Array(MAX_ENTITIES);
const shadowRotW = new Float32Array(MAX_ENTITIES);

/**
 * No history (or both sides changed at once, which is what a recycled entity id
 * looks like): a still-identity quaternion next to a non-zero Euler is someone
 * authoring angles, anything else is a real quaternion pose.
 */
function firstSightSource(entity: number): RotationSource {
  const identity =
    Transform.rotX[entity] === 0 &&
    Transform.rotY[entity] === 0 &&
    Transform.rotZ[entity] === 0 &&
    Transform.rotW[entity] === 1;
  const authoredEuler =
    Transform.eulerX[entity] !== 0 ||
    Transform.eulerY[entity] !== 0 ||
    Transform.eulerZ[entity] !== 0;
  return identity && authoredEuler ? 'euler' : 'quaternion';
}

/** Decide which representation to sync from (see {@link RotationSource}). */
export function resolveRotationSource(
  state: object,
  entity: number
): RotationSource {
  if (shadowOwner[entity] !== stateId(state)) return firstSightSource(entity);

  const eulerChanged =
    Transform.eulerX[entity] !== shadowEulerX[entity] ||
    Transform.eulerY[entity] !== shadowEulerY[entity] ||
    Transform.eulerZ[entity] !== shadowEulerZ[entity];
  const quatChanged =
    Transform.rotX[entity] !== shadowRotX[entity] ||
    Transform.rotY[entity] !== shadowRotY[entity] ||
    Transform.rotZ[entity] !== shadowRotZ[entity] ||
    Transform.rotW[entity] !== shadowRotW[entity];

  if (eulerChanged && quatChanged) return firstSightSource(entity);
  if (quatChanged) return 'quaternion';
  if (eulerChanged) return 'euler';
  return 'unchanged';
}

/** Remember the resolved pair so the next frame can tell who wrote. */
export function recordRotationShadow(state: object, entity: number): void {
  shadowEulerX[entity] = Transform.eulerX[entity];
  shadowEulerY[entity] = Transform.eulerY[entity];
  shadowEulerZ[entity] = Transform.eulerZ[entity];
  shadowRotX[entity] = Transform.rotX[entity];
  shadowRotY[entity] = Transform.rotY[entity];
  shadowRotZ[entity] = Transform.rotZ[entity];
  shadowRotW[entity] = Transform.rotW[entity];
  shadowOwner[entity] = stateId(state);
}

/** Forget an entity's rotation history (entity destroyed / id recycled). */
export function clearRotationShadow(entity: number): void {
  shadowOwner[entity] = 0;
}

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
 * Planar yaw (radians) encoded by a quaternion — the live pose of
 * physics-driven entities. Their `eulerY` mirror is only refreshed on frames
 * where the rotation changed between fixed steps, so a body walking straight
 * keeps the euler of its last turning frame; the quaternion is what the
 * renderer draws. Convention matches {@link planarYawRadians}: yaw 0 = local +Z.
 */
export function yawRadiansFromQuaternion(
  x: number,
  y: number,
  z: number,
  w: number
): number {
  return Math.atan2(2 * (w * y + x * z), 1 - 2 * (y * y + x * x));
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
