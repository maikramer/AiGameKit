import * as RAPIER from '@dimforge/rapier3d-compat';
import * as THREE from 'three';
import type { State, XMLValue } from '../../core';

export type PrimitiveKind = 'box' | 'sphere' | 'cylinder' | 'plane';

export interface PrimitiveSpec {
  readonly kind: PrimitiveKind;
  readonly posX: number;
  readonly posY: number;
  readonly posZ: number;
  readonly rotX: number;
  readonly rotY: number;
  readonly rotZ: number;
  readonly sizeX: number;
  readonly sizeY: number;
  readonly sizeZ: number;
  readonly colorR: number;
  readonly colorG: number;
  readonly colorB: number;
}

export type ColliderMode = 'auto' | 'none';

export interface CompositionData {
  readonly specs: PrimitiveSpec[];
  readonly colliderMode: ColliderMode;
}

const stateToData = new WeakMap<State, Map<number, CompositionData>>();

export function getCompositionData(
  state: State,
  entity: number
): CompositionData | undefined {
  return stateToData.get(state)?.get(entity);
}

export function setCompositionData(
  state: State,
  entity: number,
  data: CompositionData
): void {
  let m = stateToData.get(state);
  if (!m) {
    m = new Map();
    stateToData.set(state, m);
  }
  m.set(entity, data);
}

export function deleteCompositionData(state: State, entity: number): void {
  stateToData.get(state)?.delete(entity);
}

const PRIMITIVE_TAGS = new Set<string>(['box', 'sphere', 'cylinder', 'plane']);

export function isPrimitiveTag(tagName: string): boolean {
  return PRIMITIVE_TAGS.has(tagName.toLowerCase());
}

function toFloat(value: XMLValue | undefined, fallback: number): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number') return value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  const n = parseFloat(String(value));
  return Number.isNaN(n) ? fallback : n;
}

function parseVec3(
  value: XMLValue | undefined,
  fallback: [number, number, number]
): [number, number, number] {
  if (typeof value === 'string') {
    const parts = value
      .trim()
      .split(/\s+/)
      .map((p) => parseFloat(p));
    if (parts.length >= 3 && parts.every((n) => !Number.isNaN(n))) {
      return [parts[0]!, parts[1]!, parts[2]!];
    }
    if (parts.length === 1 && !Number.isNaN(parts[0])) {
      return [parts[0]!, parts[0]!, parts[0]!];
    }
  }
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]), Number(value[1]), Number(value[2])];
  }
  return fallback;
}

export function parseColorHex(value: XMLValue | undefined): [number, number, number] {
  if (typeof value !== 'string' || value.trim() === '') {
    return [0.8, 0.8, 0.8];
  }
  let hex = value.trim();
  if (hex[0] === '#') hex = hex.slice(1);
  if (hex.length === 3) {
    hex = hex
      .split('')
      .map((c) => c + c)
      .join('');
  }
  const num = parseInt(hex, 16);
  if (Number.isNaN(num)) return [0.8, 0.8, 0.8];
  return [((num >> 16) & 0xff) / 255, ((num >> 8) & 0xff) / 255, (num & 0xff) / 255];
}

const ZERO_VEC: [number, number, number] = [0, 0, 0];

export function parsePrimitiveSpec(
  tagName: string,
  attributes: Record<string, XMLValue>
): PrimitiveSpec {
  const kind = tagName.toLowerCase() as PrimitiveKind;
  const [posX, posY, posZ] = parseVec3(attributes.pos, ZERO_VEC);
  const [rotX, rotY, rotZ] = parseVec3(attributes.rotation, ZERO_VEC);
  const [sizeX, sizeY, sizeZ] = parseVec3(attributes.size, [1, 1, 1]);
  const [colorR, colorG, colorB] = parseColorHex(attributes.color);
  return {
    kind,
    posX,
    posY,
    posZ,
    rotX,
    rotY,
    rotZ,
    sizeX,
    sizeY,
    sizeZ,
    colorR,
    colorG,
    colorB,
  };
}

function primitiveGeometry(spec: PrimitiveSpec): THREE.BufferGeometry {
  switch (spec.kind) {
    case 'box':
      return new THREE.BoxGeometry(spec.sizeX, spec.sizeY, spec.sizeZ);
    case 'sphere': {
      const radius = Math.max(spec.sizeX, 1e-4);
      return new THREE.SphereGeometry(radius, 16, 12);
    }
    case 'cylinder': {
      const radiusTop = Math.max(spec.sizeX, 1e-4);
      const radiusBottom = Math.max(spec.sizeY, 1e-4);
      const height = Math.max(spec.sizeZ, 1e-4);
      return new THREE.CylinderGeometry(radiusTop, radiusBottom, height, 16);
    }
    case 'plane': {
      const width = Math.max(spec.sizeX, 1e-4);
      const height = Math.max(spec.sizeY, 1e-4);
      return new THREE.PlaneGeometry(width, height);
    }
    default:
      return new THREE.BoxGeometry(1, 1, 1);
  }
}

export function buildPrimitiveMesh(spec: PrimitiveSpec): THREE.Mesh {
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(spec.colorR, spec.colorG, spec.colorB),
    roughness: 1,
    metalness: 0,
    side:
      spec.kind === 'plane' ? THREE.DoubleSide : THREE.FrontSide,
  });
  const mesh = new THREE.Mesh(primitiveGeometry(spec), material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  mesh.position.set(spec.posX, spec.posY, spec.posZ);
  mesh.rotation.set(spec.rotX, spec.rotY, spec.rotZ);
  return mesh;
}

// Plane collider is a thin slab (Rapier has no infinite plane primitive).
const PLANE_COLLIDER_HALF_THICKNESS = 0.02;

// Descriptor is in the body's local space (body origin = entity origin), with
// size/position scaled by the entity transform.
export function buildPrimitiveColliderDesc(
  spec: PrimitiveSpec,
  scaleX: number,
  scaleY: number,
  scaleZ: number
): RAPIER.ColliderDesc {
  let desc: RAPIER.ColliderDesc;
  switch (spec.kind) {
    case 'box':
      desc = RAPIER.ColliderDesc.cuboid(
        (spec.sizeX * scaleX) / 2,
        (spec.sizeY * scaleY) / 2,
        (spec.sizeZ * scaleZ) / 2
      );
      break;
    case 'sphere': {
      const radius = Math.max(spec.sizeX, 1e-4) * Math.max(scaleX, scaleY, scaleZ);
      desc = RAPIER.ColliderDesc.ball(radius);
      break;
    }
    case 'cylinder': {
      const radiusTop = Math.max(spec.sizeX, 1e-4) * scaleX;
      const radiusBottom = Math.max(spec.sizeY, 1e-4) * scaleX;
      const radius = (radiusTop + radiusBottom) / 2;
      const height = Math.max(spec.sizeZ, 1e-4) * scaleY;
      desc = RAPIER.ColliderDesc.cylinder(height / 2, radius);
      break;
    }
    case 'plane':
      desc = RAPIER.ColliderDesc.cuboid(
        Math.max(spec.sizeX, 1e-4) * scaleX / 2,
        PLANE_COLLIDER_HALF_THICKNESS,
        Math.max(spec.sizeY, 1e-4) * scaleZ / 2
      );
      break;
    default:
      desc = RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5);
  }

  desc.setFriction(0.6);
  desc.setRestitution(0);
  desc.setTranslation(spec.posX * scaleX, spec.posY * scaleY, spec.posZ * scaleZ);

  if (spec.rotX !== 0 || spec.rotY !== 0 || spec.rotZ !== 0) {
    const quat = eulerToQuat(spec.rotX, spec.rotY, spec.rotZ);
    desc.setRotation(quat);
  }

  return desc;
}

function eulerToQuat(x: number, y: number, z: number): RAPIER.Quaternion {
  const cy = Math.cos(y * 0.5);
  const sy = Math.sin(y * 0.5);
  const cp = Math.cos(z * 0.5);
  const sp = Math.sin(z * 0.5);
  const cr = Math.cos(x * 0.5);
  const sr = Math.sin(x * 0.5);
  return new RAPIER.Quaternion(
    sr * cp * cy - cr * sp * sy,
    cr * sp * cy + sr * cp * sy,
    cr * cp * sy - sr * sp * cy,
    cr * cp * cy + sr * sp * sy
  );
}

export function parseFloatAttr(
  value: XMLValue | undefined,
  fallback: number
): number {
  return toFloat(value, fallback);
}
