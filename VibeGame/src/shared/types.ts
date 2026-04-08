/**
 * Shared type definitions for VibeGame.
 */

/** 3D vector (generic, not tied to THREE.js) */
export interface Vector3Like {
  x: number;
  y: number;
  z: number;
}

/** 2D vector */
export interface Vector2Like {
  x: number;
  y: number;
}

/** Color represented as RGBA floats [0, 1] */
export interface ColorLike {
  r: number;
  g: number;
  b: number;
  a?: number;
}

/** Axis-aligned bounding box */
export interface AABB {
  min: Vector3Like;
  max: Vector3Like;
}

/** Quaternion */
export interface QuaternionLike {
  x: number;
  y: number;
  z: number;
  w: number;
}

/** 4x4 matrix (column-major) */
export type Matrix4 = number[];

/** Entity reference (ECS entity id) */
export type EntityId = number;

/** Callback with no arguments and no return */
export type Noop = () => void;

/** Disposable resource */
export interface Disposable {
  dispose(): void;
}

/** Named entity reference */
export interface EntityRef {
  readonly name: string;
}

/** Timing information */
export interface TimingInfo {
  delta: number;
  elapsed: number;
  fixedDelta: number;
}
