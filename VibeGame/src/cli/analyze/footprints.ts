import { readFileSync } from 'node:fs';
import { flattenNumberList, splitNumbers } from '../../core';
import type { ParsedElement, XMLValue } from '../../core';
import { loadGlbCollisionMesh } from '../../plugins/physics/mesh-collider';
import {
  parseAt,
  parseSemicolonPlaceString,
} from '../../plugins/spawner/place-fields';
import { resolveAssetPath } from './assets';
import type { AnalyzeIssue, Footprint } from './types';

const SOLID_PRIMS = new Set(['box', 'sphere', 'cylinder']);

function attrStr(v: XMLValue | undefined): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v === 'string') {
    const t = v.trim();
    return t === '' ? null : t;
  }
  if (typeof v === 'number') return String(v);
  return null;
}

function attrFlatNumbers(v: XMLValue | undefined): number[] {
  return v === undefined || v === null ? [] : flattenNumberList(v);
}

function parseVec3(
  value: XMLValue | undefined,
  fallback: [number, number, number]
): [number, number, number] {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'object' && !Array.isArray(value)) {
    const o = value as Record<string, number>;
    return [
      Number(o.x ?? fallback[0]),
      Number(o.y ?? fallback[1]),
      Number(o.z ?? fallback[2]),
    ];
  }
  if (typeof value === 'string') {
    const p = value
      .trim()
      .split(/\s+/)
      .map((x) => parseFloat(x));
    if (p.length >= 3 && p.every((n) => !Number.isNaN(n))) {
      return [p[0]!, p[1]!, p[2]!];
    }
    if (p.length === 2 && p.every((n) => !Number.isNaN(n))) {
      return [p[0]!, fallback[1], p[1]!];
    }
  }
  return fallback;
}

/** Root transform="rotation: x y z" is degrees; child Box rotation is radians. */
function parseRootYawDegrees(transformRaw: XMLValue | undefined): number {
  const s = attrStr(transformRaw);
  if (!s) return 0;
  const m = s.match(/rotation\s*:\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)/i);
  if (!m) return 0;
  return parseFloat(m[2]!);
}

function parsePlaceXZ(placeRaw: XMLValue | undefined): [number, number] | null {
  const placeStr = attrStr(placeRaw);
  if (!placeStr) return null;
  try {
    const merged = parseSemicolonPlaceString(placeStr);
    if (!('at' in merged)) return null;
    return parseAt(merged.at);
  } catch {
    return null;
  }
}

/** XML `overlap-max` metres; missing/invalid → 0 (strict). */
function parseOverlapMax(raw: XMLValue | undefined): number {
  const s = attrStr(raw);
  if (!s) return 0;
  const n = parseFloat(s);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function expandRotatedRect(
  cx: number,
  cz: number,
  halfX: number,
  halfZ: number,
  yawRad: number,
  into: { minX: number; maxX: number; minZ: number; maxZ: number }
): void {
  const c = Math.cos(yawRad);
  const s = Math.sin(yawRad);
  const corners: [number, number][] = [
    [-halfX, -halfZ],
    [halfX, -halfZ],
    [halfX, halfZ],
    [-halfX, halfZ],
  ];
  for (const [lx, lz] of corners) {
    const wx = cx + lx * c - lz * s;
    const wz = cz + lx * s + lz * c;
    into.minX = Math.min(into.minX, wx);
    into.maxX = Math.max(into.maxX, wx);
    into.minZ = Math.min(into.minZ, wz);
    into.maxZ = Math.max(into.maxZ, wz);
  }
}

function emptyUnion(): {
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
} {
  return {
    minX: Infinity,
    maxX: -Infinity,
    minZ: Infinity,
    maxZ: -Infinity,
  };
}

type Bounds3 = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
};

/** Per-analyze-run cache: same URL reused across many GameObjects. */
const boundsCache = new Map<string, Bounds3 | null>();

async function glbBounds(
  publicDir: string,
  url: string,
  issues: AnalyzeIssue[],
  label: string
): Promise<Bounds3 | null> {
  const file = resolveAssetPath(publicDir, url);
  if (!file) return null;
  const cacheKey = file;
  if (boundsCache.has(cacheKey)) return boundsCache.get(cacheKey)!;
  try {
    const buf = readFileSync(file);
    const ab = buf.buffer.slice(
      buf.byteOffset,
      buf.byteOffset + buf.byteLength
    );
    const mesh = await loadGlbCollisionMesh(ab, url);
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const p = mesh.vertices;
    for (let i = 0; i < p.length; i += 3) {
      const x = p[i]!;
      const y = p[i + 1]!;
      const z = p[i + 2]!;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
    if (!Number.isFinite(minX)) {
      boundsCache.set(cacheKey, null);
      return null;
    }
    const bounds = { minX, maxX, minY, maxY, minZ, maxZ };
    boundsCache.set(cacheKey, bounds);
    return bounds;
  } catch (e) {
    issues.push({
      severity: 'warn',
      code: 'bounds',
      message: `[analyze] cannot read GLB bounds for ${label}: ${url} (${e instanceof Error ? e.message : e})`,
    });
    boundsCache.set(cacheKey, null);
    return null;
  }
}

function parseColliderBoxSize(
  colliderRaw: string
): [number, number, number] | null {
  const m = colliderRaw.match(
    /size\s*:\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)/i
  );
  if (!m) return null;
  return [parseFloat(m[1]!), parseFloat(m[2]!), parseFloat(m[3]!)];
}

function parseColliderMeshUrl(colliderRaw: string): string | null {
  const m = colliderRaw.match(/mesh-url\s*:\s*([^;]+)/i);
  return m ? m[1]!.trim() : null;
}

function colliderIsNone(colliderRaw: string | null): boolean {
  if (!colliderRaw) return false;
  return (
    /(^|;)\s*none\s*($|;)/i.test(colliderRaw) ||
    /shape\s*:\s*none/i.test(colliderRaw)
  );
}

let _fpSeq = 0;

function makeId(prefix: string): string {
  _fpSeq += 1;
  return `${prefix}-${_fpSeq}`;
}

/**
 * One footprint per solid primitive (not a filled union AABB).
 * Wall rings stay thin segments; same Composition shares groupId.
 */
function compositionFootprints(
  el: ParsedElement,
  parentPath: string,
  _publicDir: string,
  _issues: AnalyzeIssue[]
): Footprint[] {
  const collider = attrStr(el.attributes.collider);
  if (colliderIsNone(collider)) return [];

  const place = parsePlaceXZ(el.attributes.place);
  if (!place) return [];
  const [atX, atZ] = place;
  const yawDeg = parseRootYawDegrees(el.attributes.transform);
  const yawRad = (yawDeg * Math.PI) / 180;

  const name = attrStr(el.attributes.name);
  const overlapMax = parseOverlapMax(el.attributes['overlap-max']);
  const baseLabel =
    name ??
    `Composition@${parentPath || 'world'} place=(${atX.toFixed(1)}, ${atZ.toFixed(1)})`;
  const groupId = makeId('comp-group');
  const out: Footprint[] = [];
  let part = 0;

  for (const child of el.children) {
    const kind = child.tagName.toLowerCase();
    if (!SOLID_PRIMS.has(kind)) continue;

    const [px, py, pz] = parseVec3(child.attributes.pos, [0, 0, 0]);
    const [, childYawRad] = (() => {
      const [rx, ry, rz] = parseVec3(child.attributes.rotation, [0, 0, 0]);
      void rx;
      void rz;
      return [0, ry, 0] as const;
    })();
    const totalYaw = yawRad + childYawRad;
    const box = emptyUnion();
    let minY: number;
    let maxY: number;

    if (kind === 'box') {
      const [sx, sy, sz] = parseVec3(child.attributes.size, [1, 1, 1]);
      expandRotatedRect(
        atX + px,
        atZ + pz,
        Math.abs(sx) / 2,
        Math.abs(sz) / 2,
        totalYaw,
        box
      );
      minY = py - Math.abs(sy) / 2;
      maxY = py + Math.abs(sy) / 2;
    } else if (kind === 'cylinder') {
      const [rTop, rBot, h] = parseVec3(child.attributes.size, [0.5, 0.5, 1]);
      const r = Math.max(Math.abs(rTop), Math.abs(rBot));
      const height = Math.abs(h);
      box.minX = atX + px - r;
      box.maxX = atX + px + r;
      box.minZ = atZ + pz - r;
      box.maxZ = atZ + pz + r;
      minY = py - height / 2;
      maxY = py + height / 2;
    } else if (kind === 'sphere') {
      const [r] = parseVec3(child.attributes.size, [0.5, 0.5, 0.5]);
      const rad = Math.abs(r);
      box.minX = atX + px - rad;
      box.maxX = atX + px + rad;
      box.minZ = atZ + pz - rad;
      box.maxZ = atZ + pz + rad;
      minY = py - rad;
      maxY = py + rad;
    } else {
      continue;
    }

    part += 1;
    out.push({
      id: makeId('comp'),
      label: part > 1 ? `${baseLabel}#${part}` : baseLabel,
      minX: box.minX,
      maxX: box.maxX,
      minY,
      maxY,
      minZ: box.minZ,
      maxZ: box.maxZ,
      kind: 'composition',
      groupId,
      ...(overlapMax > 0 ? { overlapMax } : {}),
    });
  }

  return out;
}

function compositionGroundFootprints(
  el: ParsedElement,
  parentPath: string
): Footprint[] {
  const place = parsePlaceXZ(el.attributes.place);
  if (!place) return [];
  const [atX, atZ] = place;
  const yawDeg = parseRootYawDegrees(el.attributes.transform);
  const yawRad = (yawDeg * Math.PI) / 180;
  const name = attrStr(el.attributes.name);
  const baseLabel =
    name ??
    `Pad@${parentPath || 'world'} place=(${atX.toFixed(1)}, ${atZ.toFixed(1)})`;
  const out: Footprint[] = [];

  for (const child of el.children) {
    const kind = child.tagName.toLowerCase();
    if (kind !== 'pad' && kind !== 'plane') continue;
    const [px, py, pz] = parseVec3(child.attributes.pos, [0, 0, 0]);
    // Pad size is width depth (2-comp) or width height depth. XMLValueParser
    // pre-converts "16 12" into {x:16,y:12} and 3-comp into {x,y,z} — handle
    // every shape; the old string-only read left every pad at 0.5 half-extent.
    const sizeRaw = child.attributes.size;
    let halfX = 0.5;
    let halfZ = 0.5;
    if (typeof sizeRaw === 'object' && sizeRaw !== null) {
      const o = sizeRaw as Record<string, number>;
      if (
        typeof o.x === 'number' &&
        (typeof o.y === 'number' || typeof o.z === 'number')
      ) {
        halfX = Math.abs(o.x) / 2;
        halfZ = Math.abs(typeof o.z === 'number' ? o.z : o.y) / 2;
      }
    } else if (typeof sizeRaw === 'string') {
      const p = splitNumbers(sizeRaw);
      if (p.length >= 2) {
        halfX = Math.abs(p[0]!) / 2;
        halfZ = Math.abs(p[p.length >= 3 ? 2 : 1]!) / 2;
      }
    }
    const box = emptyUnion();
    expandRotatedRect(atX + px, atZ + pz, halfX, halfZ, yawRad, box);
    out.push({
      id: makeId('pad'),
      label: baseLabel,
      minX: box.minX,
      maxX: box.maxX,
      minY: py,
      maxY: py,
      minZ: box.minZ,
      maxZ: box.maxZ,
      kind: 'pad',
    });
  }
  return out;
}

async function gameObjectFootprint(
  el: ParsedElement,
  parentPath: string,
  publicDir: string,
  issues: AnalyzeIssue[]
): Promise<Footprint | null> {
  const collider = attrStr(el.attributes.collider);
  if (colliderIsNone(collider)) return null;

  const place = parsePlaceXZ(el.attributes.place);
  if (!place) return null;
  const [atX, atZ] = place;
  const yawDeg = parseRootYawDegrees(el.attributes.transform);
  const yawRad = (yawDeg * Math.PI) / 180;
  const name = attrStr(el.attributes.name);
  const overlapMax = parseOverlapMax(el.attributes['overlap-max']);
  const label =
    name ??
    `GameObject@${parentPath || 'world'} place=(${atX.toFixed(1)}, ${atZ.toFixed(1)})`;
  const withMax = (fp: Footprint): Footprint =>
    overlapMax > 0 ? { ...fp, overlapMax } : fp;

  if (collider) {
    const size = parseColliderBoxSize(collider);
    if (size) {
      const [sx, sy, sz] = size;
      const box = emptyUnion();
      expandRotatedRect(
        atX,
        atZ,
        Math.abs(sx) / 2,
        Math.abs(sz) / 2,
        yawRad,
        box
      );
      return withMax({
        id: makeId('go'),
        label,
        minX: box.minX,
        maxX: box.maxX,
        minY: -Math.abs(sy) / 2,
        maxY: Math.abs(sy) / 2,
        minZ: box.minZ,
        maxZ: box.maxZ,
        kind: 'gameobject',
      });
    }
    const meshUrl = parseColliderMeshUrl(collider);
    if (meshUrl) {
      const local = await glbBounds(publicDir, meshUrl, issues, label);
      if (local) {
        const cx = (local.minX + local.maxX) / 2;
        const cz = (local.minZ + local.maxZ) / 2;
        const hx = (local.maxX - local.minX) / 2;
        const hz = (local.maxZ - local.minZ) / 2;
        const world = emptyUnion();
        expandRotatedRect(atX + cx, atZ + cz, hx, hz, yawRad, world);
        return withMax({
          id: makeId('go'),
          label,
          minX: world.minX,
          maxX: world.maxX,
          minY: local.minY,
          maxY: local.maxY,
          minZ: world.minZ,
          maxZ: world.maxZ,
          kind: 'gameobject',
        });
      }
    }
  }

  // Falling back to the model's visual bounds only makes sense for something
  // that actually collides. A decoration with neither `collider` nor
  // `rigidbody` is scenery, and treating its silhouette as solid reports the
  // colliders deliberately placed *inside* it as overlaps — that is how the
  // desert arch (visual-only mesh + two pillar boxes for the legs, so the
  // player can ride through the opening) ended up as two hard errors.
  const hasPhysics = !!collider || !!attrStr(el.attributes.rigidbody);
  if (!hasPhysics) return null;

  for (const child of el.children) {
    if (child.tagName.toLowerCase() !== 'gltfloader') continue;
    const url = attrStr(child.attributes.url);
    if (!url) continue;
    const local = await glbBounds(publicDir, url, issues, label);
    if (!local) {
      issues.push({
        severity: 'warn',
        code: 'bounds',
        message: `[analyze] no solid bounds for ${label} (${url})`,
      });
      return null;
    }
    const cx = (local.minX + local.maxX) / 2;
    const cz = (local.minZ + local.maxZ) / 2;
    const hx = (local.maxX - local.minX) / 2;
    const hz = (local.maxZ - local.minZ) / 2;
    const world = emptyUnion();
    expandRotatedRect(atX + cx, atZ + cz, hx, hz, yawRad, world);
    return withMax({
      id: makeId('go'),
      label,
      minX: world.minX,
      maxX: world.maxX,
      minY: local.minY,
      maxY: local.maxY,
      minZ: world.minZ,
      maxZ: world.maxZ,
      kind: 'gameobject',
    });
  }

  if (collider && /trimesh|mesh-url/i.test(collider)) {
    issues.push({
      severity: 'warn',
      code: 'bounds',
      message: `[analyze] no solid bounds for ${label}`,
    });
  }
  return null;
}

function roadFootprint(
  el: ParsedElement,
  parentPath: string
): Footprint | null {
  const nums = attrFlatNumbers(el.attributes.path);
  if (nums.length < 4) return null;
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i + 1 < nums.length; i += 2) {
    const x = nums[i]!;
    const z = nums[i + 1]!;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minZ = Math.min(minZ, z);
    maxZ = Math.max(maxZ, z);
  }
  const width = parseFloat(attrStr(el.attributes.width) ?? '2') || 2;
  const half = Math.abs(width) / 2;
  const name = attrStr(el.attributes.name);
  return {
    id: makeId('road'),
    label: name ?? `Road@${parentPath || 'world'}`,
    minX: minX - half,
    maxX: maxX + half,
    minY: 0,
    maxY: 0,
    minZ: minZ - half,
    maxZ: maxZ + half,
    kind: 'road',
  };
}

function standalonePadFootprint(
  el: ParsedElement,
  parentPath: string
): Footprint | null {
  const place = parsePlaceXZ(el.attributes.place);
  const [px, py, pz] = parseVec3(el.attributes.pos, [0, 0, 0]);
  const atX = place ? place[0] + px : px;
  const atZ = place ? place[1] + pz : pz;
  const sizeRaw = el.attributes.size;
  let halfX = 0.5;
  let halfZ = 0.5;
  if (typeof sizeRaw === 'string') {
    const p = sizeRaw.trim().split(/\s+/).map(Number);
    if (p.length >= 2 && p.every((n) => !Number.isNaN(n))) {
      halfX = Math.abs(p[0]!) / 2;
      halfZ = Math.abs(p[p.length >= 3 ? 2 : 1]!) / 2;
    }
  }
  const name = attrStr(el.attributes.name);
  return {
    id: makeId('pad'),
    label: name ?? `Pad@${parentPath || 'world'}`,
    minX: atX - halfX,
    maxX: atX + halfX,
    minY: py,
    maxY: py,
    minZ: atZ - halfZ,
    maxZ: atZ + halfZ,
    kind: 'pad',
  };
}

/**
 * Walk expanded world tree; collect solid + ground (Pad/Road) footprints.
 */
export async function collectFootprints(
  root: ParsedElement,
  publicDir: string,
  issues: AnalyzeIssue[]
): Promise<Footprint[]> {
  _fpSeq = 0;
  boundsCache.clear();
  const out: Footprint[] = [];

  const walk = async (el: ParsedElement, groupPath: string) => {
    const tag = el.tagName.toLowerCase();
    const name = attrStr(el.attributes.name);
    const nextPath =
      tag === 'group' && name
        ? groupPath
          ? `${groupPath}/${name}`
          : name
        : groupPath;

    if (tag === 'composition') {
      out.push(...compositionFootprints(el, nextPath, publicDir, issues));
      out.push(...compositionGroundFootprints(el, nextPath));
      for (const c of el.children) {
        const ct = c.tagName.toLowerCase();
        if (!SOLID_PRIMS.has(ct) && ct !== 'pad' && ct !== 'plane') {
          await walk(c, nextPath);
        }
      }
      return;
    }

    if (tag === 'gameobject') {
      const fp = await gameObjectFootprint(el, nextPath, publicDir, issues);
      if (fp) out.push(fp);
      return;
    }

    if (tag === 'road') {
      const fp = roadFootprint(el, nextPath);
      if (fp) out.push(fp);
      return;
    }

    if (tag === 'pad') {
      const fp = standalonePadFootprint(el, nextPath);
      if (fp) out.push(fp);
      return;
    }

    for (const c of el.children) await walk(c, nextPath);
  };

  await walk(root, '');
  return out;
}

export function solidFootprintCount(footprints: Footprint[]): number {
  return footprints.filter((f) => f.kind !== 'pad' && f.kind !== 'road').length;
}
