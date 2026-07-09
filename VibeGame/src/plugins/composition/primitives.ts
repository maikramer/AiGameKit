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
  // Textura opcional (URL site-root, ex.: "/assets/textures/foo.png").
  // Quando ausente, a primitiva usa cor flat (colorR/G/B).
  readonly textureUrl: string | null;
  // Repetição da textura (UV repeat em X/Y). Default 1 1.
  readonly textureRepeatX: number;
  readonly textureRepeatY: number;
  // Rotação da textura em radianos (UV rotation). Default 0.
  readonly textureRotation: number;
  // Normal/roughness/AO maps opcionais (PBR), mesmas UVs do map.
  readonly normalMapUrl: string | null;
  readonly roughnessMapUrl: string | null;
  // Roughness/metalness fixos (defaults conservadores para materiais têxteis).
  readonly roughness: number;
  readonly metalness: number;
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

// XMLValueParser (core/xml/values) already converts vector strings like
// "0 1.6 -3" into `{x,y,z}` objects and hex colors like "#6b4a2b" / "0x..."
// into numbers, so accept every shape here instead of assuming strings.
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
    return fallback;
  }
  if (typeof value === 'number' && !Number.isNaN(value)) {
    return [value, value, value];
  }
  if (Array.isArray(value) && value.length >= 3) {
    return [Number(value[0]), Number(value[1]), Number(value[2])];
  }
  if (value && typeof value === 'object') {
    const v = value as Record<string, unknown>;
    const x = Number(v.x);
    const y = Number(v.y);
    const z = Number(v.z ?? v.w);
    if (!Number.isNaN(x) && !Number.isNaN(y) && !Number.isNaN(z)) {
      return [x, y, z];
    }
  }
  return fallback;
}

export function parseColorHex(
  value: XMLValue | undefined
): [number, number, number] {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return [
      ((value >> 16) & 0xff) / 255,
      ((value >> 8) & 0xff) / 255,
      (value & 0xff) / 255,
    ];
  }
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
  return [
    ((num >> 16) & 0xff) / 255,
    ((num >> 8) & 0xff) / 255,
    (num & 0xff) / 255,
  ];
}

const ZERO_VEC: [number, number, number] = [0, 0, 0];

function parseTextureRepeat(value: XMLValue | undefined): [number, number] {
  if (value === undefined || value === null) return [1, 1];
  if (typeof value === 'number') return [value, value];
  const parts = String(value)
    .trim()
    .split(/\s+/)
    .map((p) => parseFloat(p));
  if (parts.length >= 2 && parts.every((n) => !Number.isNaN(n))) {
    return [parts[0]!, parts[1]!];
  }
  if (parts.length === 1 && !Number.isNaN(parts[0])) {
    return [parts[0]!, parts[0]!];
  }
  return [1, 1];
}

export function parsePrimitiveSpec(
  tagName: string,
  attributes: Record<string, XMLValue>
): PrimitiveSpec {
  const kind = tagName.toLowerCase() as PrimitiveKind;
  const [posX, posY, posZ] = parseVec3(attributes.pos, ZERO_VEC);
  const [rotX, rotY, rotZ] = parseVec3(attributes.rotation, ZERO_VEC);
  const [sizeX, sizeY, sizeZ] = parseVec3(attributes.size, [1, 1, 1]);
  const [colorR, colorG, colorB] = parseColorHex(attributes.color);
  // Textura opcional: aceitar tanto texture-url como map-url (alias).
  const textureUrlRaw =
    attributes['texture-url'] ?? attributes['map-url'] ?? attributes.texture;
  const textureUrl =
    typeof textureUrlRaw === 'string' && textureUrlRaw.trim() !== ''
      ? textureUrlRaw.trim()
      : null;
  const [textureRepeatX, textureRepeatY] = parseTextureRepeat(
    attributes['texture-repeat']
  );
  const textureRotation = toFloat(attributes['texture-rotation'], 0);
  const normalMapUrlRaw =
    attributes['normal-map-url'] ?? attributes['normal-url'];
  const normalMapUrl =
    typeof normalMapUrlRaw === 'string' && normalMapUrlRaw.trim() !== ''
      ? normalMapUrlRaw.trim()
      : null;
  const roughnessMapUrlRaw =
    attributes['roughness-map-url'] ?? attributes['roughness-url'];
  const roughnessMapUrl =
    typeof roughnessMapUrlRaw === 'string' && roughnessMapUrlRaw.trim() !== ''
      ? roughnessMapUrlRaw.trim()
      : null;
  // Roughness/metalness ajustáveis (defaults conservadores).
  const roughness = Math.max(0, Math.min(1, toFloat(attributes.roughness, 1)));
  const metalness = Math.max(0, Math.min(1, toFloat(attributes.metalness, 0)));
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
    textureUrl,
    textureRepeatX,
    textureRepeatY,
    textureRotation,
    normalMapUrl,
    roughnessMapUrl,
    roughness,
    metalness,
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

// Cache de texturas partilhado por todas as composições (mesmo padrão do
// terrain/sky: module-level TextureLoader reutilizado, cache por URL).
// Cada URL é carregado uma única vez; a repetição/rotação UV é aplicada por
// material via texture.clone() quando uma primitiva precisa de UVs diferentes.
const _compositionTextureLoader = new THREE.TextureLoader();
const _compositionTextureCache = new Map<string, THREE.Texture>();

function loadCompositionTexture(url: string): THREE.Texture {
  const cached = _compositionTextureCache.get(url);
  if (cached) return cached;
  const tex = _compositionTextureLoader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _compositionTextureCache.set(url, tex);
  return tex;
}

function loadCompositionDataTexture(url: string): THREE.Texture {
  // Texturas de dados (normal/roughness/AO) vivem em espaço linear.
  const cached = _compositionTextureCache.get(url);
  if (cached) return cached;
  const tex = _compositionTextureLoader.load(url);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.LinearSRGBColorSpace;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _compositionTextureCache.set(url, tex);
  return tex;
}

export function buildPrimitiveMesh(spec: PrimitiveSpec): THREE.Mesh {
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color(spec.colorR, spec.colorG, spec.colorB),
    roughness: spec.roughness,
    metalness: spec.metalness,
    side: spec.kind === 'plane' ? THREE.DoubleSide : THREE.FrontSide,
  });

  // Aplicar textura albedo (map) quando definida. Usamos clone() para que cada
  // primitiva possa ter repeat/rotation UV independente sem afetar as irmãs
  // que partilham o mesmo URL (a imagem GPU é partilhada pelo cache).
  if (spec.textureUrl) {
    try {
      const baseTex = loadCompositionTexture(spec.textureUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.map = tex;
    } catch {
      // Se a textura falhar a carregar (URL inválido), cai para cor flat.
    }
  }
  if (spec.normalMapUrl) {
    try {
      const baseTex = loadCompositionDataTexture(spec.normalMapUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.normalMap = tex;
    } catch {
      // ignore
    }
  }
  if (spec.roughnessMapUrl) {
    try {
      const baseTex = loadCompositionDataTexture(spec.roughnessMapUrl);
      const tex = baseTex.clone();
      tex.repeat.set(spec.textureRepeatX, spec.textureRepeatY);
      tex.rotation = spec.textureRotation;
      tex.center.set(0.5, 0.5);
      tex.needsUpdate = true;
      material.roughnessMap = tex;
    } catch {
      // ignore
    }
  }

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
      const radius =
        Math.max(spec.sizeX, 1e-4) * Math.max(scaleX, scaleY, scaleZ);
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
        (Math.max(spec.sizeX, 1e-4) * scaleX) / 2,
        PLANE_COLLIDER_HALF_THICKNESS,
        (Math.max(spec.sizeY, 1e-4) * scaleZ) / 2
      );
      break;
    default:
      desc = RAPIER.ColliderDesc.cuboid(0.5, 0.5, 0.5);
  }

  desc.setFriction(0.6);
  desc.setRestitution(0);
  desc.setTranslation(
    spec.posX * scaleX,
    spec.posY * scaleY,
    spec.posZ * scaleZ
  );

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
