/**
 * Math utilities for Vector3, Vector2, and generic operations.
 * Lightweight helpers — not a full linear algebra library.
 */

import type { Vector3Like, Vector2Like, AABB } from './types';

// --- Vector3 ---

export const vec3 = {
  create(x = 0, y = 0, z = 0): Vector3Like {
    return { x, y, z };
  },

  zero(): Vector3Like {
    return { x: 0, y: 0, z: 0 };
  },

  one(): Vector3Like {
    return { x: 1, y: 1, z: 1 };
  },

  clone(v: Vector3Like): Vector3Like {
    return { x: v.x, y: v.y, z: v.z };
  },

  set(v: Vector3Like, x: number, y: number, z: number): Vector3Like {
    v.x = x;
    v.y = y;
    v.z = z;
    return v;
  },

  add(a: Vector3Like, b: Vector3Like): Vector3Like {
    return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
  },

  sub(a: Vector3Like, b: Vector3Like): Vector3Like {
    return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
  },

  scale(v: Vector3Like, s: number): Vector3Like {
    return { x: v.x * s, y: v.y * s, z: v.z * s };
  },

  multiply(a: Vector3Like, b: Vector3Like): Vector3Like {
    return { x: a.x * b.x, y: a.y * b.y, z: a.z * b.z };
  },

  dot(a: Vector3Like, b: Vector3Like): number {
    return a.x * b.x + a.y * b.y + a.z * b.z;
  },

  cross(a: Vector3Like, b: Vector3Like): Vector3Like {
    return {
      x: a.y * b.z - a.z * b.y,
      y: a.z * b.x - a.x * b.z,
      z: a.x * b.y - a.y * b.x,
    };
  },

  length(v: Vector3Like): number {
    return Math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  },

  lengthSq(v: Vector3Like): number {
    return v.x * v.x + v.y * v.y + v.z * v.z;
  },

  normalize(v: Vector3Like): Vector3Like {
    const len = vec3.length(v);
    return len > 0 ? vec3.scale(v, 1 / len) : vec3.zero();
  },

  distance(a: Vector3Like, b: Vector3Like): number {
    return vec3.length(vec3.sub(a, b));
  },

  lerp(a: Vector3Like, b: Vector3Like, t: number): Vector3Like {
    return {
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
      z: a.z + (b.z - a.z) * t,
    };
  },

  equals(a: Vector3Like, b: Vector3Like, epsilon = 1e-6): boolean {
    return (
      Math.abs(a.x - b.x) < epsilon &&
      Math.abs(a.y - b.y) < epsilon &&
      Math.abs(a.z - b.z) < epsilon
    );
  },
};

// --- Vector2 ---

export const vec2 = {
  create(x = 0, y = 0): Vector2Like {
    return { x, y };
  },

  clone(v: Vector2Like): Vector2Like {
    return { x: v.x, y: v.y };
  },

  add(a: Vector2Like, b: Vector2Like): Vector2Like {
    return { x: a.x + b.x, y: a.y + b.y };
  },

  sub(a: Vector2Like, b: Vector2Like): Vector2Like {
    return { x: a.x - b.x, y: a.y - b.y };
  },

  scale(v: Vector2Like, s: number): Vector2Like {
    return { x: v.x * s, y: v.y * s };
  },

  dot(a: Vector2Like, b: Vector2Like): number {
    return a.x * b.x + a.y * b.y;
  },

  length(v: Vector2Like): number {
    return Math.sqrt(v.x * v.x + v.y * v.y);
  },

  lerp(a: Vector2Like, b: Vector2Like, t: number): Vector2Like {
    return {
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
    };
  },
};

// --- AABB ---

export const aabb = {
  create(
    minX: number,
    minY: number,
    minZ: number,
    maxX: number,
    maxY: number,
    maxZ: number
  ): AABB {
    return {
      min: { x: minX, y: minY, z: minZ },
      max: { x: maxX, y: maxY, z: maxZ },
    };
  },

  contains(box: AABB, point: Vector3Like): boolean {
    return (
      point.x >= box.min.x &&
      point.x <= box.max.x &&
      point.y >= box.min.y &&
      point.y <= box.max.y &&
      point.z >= box.min.z &&
      point.z <= box.max.z
    );
  },

  intersects(a: AABB, b: AABB): boolean {
    return (
      a.min.x <= b.max.x &&
      a.max.x >= b.min.x &&
      a.min.y <= b.max.y &&
      a.max.y >= b.min.y &&
      a.min.z <= b.max.z &&
      a.max.z >= b.min.z
    );
  },
};

// --- General ---

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export function clamp01(value: number): number {
  return clamp(value, 0, 1);
}

export function degToRad(deg: number): number {
  return deg * (Math.PI / 180);
}

export function radToDeg(rad: number): number {
  return rad * (180 / Math.PI);
}

export function mapRange(
  value: number,
  inMin: number,
  inMax: number,
  outMin: number,
  outMax: number
): number {
  return outMin + ((value - inMin) / (inMax - inMin)) * (outMax - outMin);
}

export function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
}
