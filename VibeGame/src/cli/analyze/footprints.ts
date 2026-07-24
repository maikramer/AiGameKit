import { readFileSync } from 'node:fs';
import * as path from 'node:path';
import type { ParsedElement, XMLValue } from '../../core';
import { parseGlbCollisionMesh } from '../../plugins/physics/mesh-collider';
import {
  parseAt,
  parseSemicolonPlaceString,
} from '../../plugins/spawner/place-fields';
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

function resolvePublicPath(publicDir: string, url: string): string | null {
  const t = url.trim();
  if (!t.startsWith('/')) return null;
  return path.join(publicDir, t.replace(/^\//, ''));
}

function glbXZBounds(
  publicDir: string,
  url: string,
  issues: AnalyzeIssue[],
  label: string
): { minX: number; maxX: number; minZ: number; maxZ: number } | null {
  const file = resolvePublicPath(publicDir, url);
  if (!file) return null;
  try {
    const buf = readFileSync(file);
    const mesh = parseGlbCollisionMesh(
      buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength)
    );
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const p = mesh.vertices;
    for (let i = 0; i < p.length; i += 3) {
      const x = p[i]!;
      const z = p[i + 2]!;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
    if (!Number.isFinite(minX)) return null;
    return { minX, maxX, minZ, maxZ };
  } catch (e) {
    issues.push({
      severity: 'warn',
      code: 'bounds',
      message: `[analyze] cannot read GLB bounds for ${label}: ${url} (${e instanceof Error ? e.message : e})`,
    });
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
  const baseLabel =
    name ??
    `Composition@${parentPath || 'world'} place=(${atX.toFixed(1)}, ${atZ.toFixed(1)})`;
  const groupId = makeId('comp-group');
  const out: Footprint[] = [];
  let part = 0;

  for (const child of el.children) {
    const kind = child.tagName.toLowerCase();
    if (!SOLID_PRIMS.has(kind)) continue;

    const [px, , pz] = parseVec3(child.attributes.pos, [0, 0, 0]);
    const [, childYawRad] = (() => {
      const [rx, ry, rz] = parseVec3(child.attributes.rotation, [0, 0, 0]);
      void rx;
      void rz;
      return [0, ry, 0] as const;
    })();
    const totalYaw = yawRad + childYawRad;
    const box = emptyUnion();

    if (kind === 'box') {
      const [sx, , sz] = parseVec3(child.attributes.size, [1, 1, 1]);
      expandRotatedRect(
        atX + px,
        atZ + pz,
        Math.abs(sx) / 2,
        Math.abs(sz) / 2,
        totalYaw,
        box
      );
    } else if (kind === 'cylinder') {
      const [rTop, rBot] = parseVec3(child.attributes.size, [0.5, 0.5, 1]);
      const r = Math.max(Math.abs(rTop), Math.abs(rBot));
      box.minX = atX + px - r;
      box.maxX = atX + px + r;
      box.minZ = atZ + pz - r;
      box.maxZ = atZ + pz + r;
    } else if (kind === 'sphere') {
      const [r] = parseVec3(child.attributes.size, [0.5, 0.5, 0.5]);
      const rad = Math.abs(r);
      box.minX = atX + px - rad;
      box.maxX = atX + px + rad;
      box.minZ = atZ + pz - rad;
      box.maxZ = atZ + pz + rad;
    } else {
      continue;
    }

    part += 1;
    out.push({
      id: makeId('comp'),
      label: part > 1 ? `${baseLabel}#${part}` : baseLabel,
      minX: box.minX,
      maxX: box.maxX,
      minZ: box.minZ,
      maxZ: box.maxZ,
      kind: 'composition',
      groupId,
    });
  }

  return out;
}

function gameObjectFootprint(
  el: ParsedElement,
  parentPath: string,
  publicDir: string,
  issues: AnalyzeIssue[]
): Footprint | null {
  const collider = attrStr(el.attributes.collider);
  if (colliderIsNone(collider)) return null;

  const place = parsePlaceXZ(el.attributes.place);
  if (!place) return null;
  const [atX, atZ] = place;
  const yawDeg = parseRootYawDegrees(el.attributes.transform);
  const yawRad = (yawDeg * Math.PI) / 180;
  const name = attrStr(el.attributes.name);
  const label =
    name ??
    `GameObject@${parentPath || 'world'} place=(${atX.toFixed(1)}, ${atZ.toFixed(1)})`;

  // Prefer explicit box collider size
  if (collider) {
    const size = parseColliderBoxSize(collider);
    if (size) {
      const [sx, , sz] = size;
      const box = emptyUnion();
      expandRotatedRect(
        atX,
        atZ,
        Math.abs(sx) / 2,
        Math.abs(sz) / 2,
        yawRad,
        box
      );
      return {
        id: makeId('go'),
        label,
        minX: box.minX,
        maxX: box.maxX,
        minZ: box.minZ,
        maxZ: box.maxZ,
        kind: 'gameobject',
      };
    }
    const meshUrl = parseColliderMeshUrl(collider);
    if (meshUrl) {
      const local = glbXZBounds(publicDir, meshUrl, issues, label);
      if (local) {
        const cx = (local.minX + local.maxX) / 2;
        const cz = (local.minZ + local.maxZ) / 2;
        const hx = (local.maxX - local.minX) / 2;
        const hz = (local.maxZ - local.minZ) / 2;
        const world = emptyUnion();
        expandRotatedRect(atX + cx, atZ + cz, hx, hz, yawRad, world);
        return {
          id: makeId('go'),
          label,
          minX: world.minX,
          maxX: world.maxX,
          minZ: world.minZ,
          maxZ: world.maxZ,
          kind: 'gameobject',
        };
      }
    }
  }

  // Fallback: first GLTFLoader url
  for (const child of el.children) {
    if (child.tagName.toLowerCase() !== 'gltfloader') continue;
    const url = attrStr(child.attributes.url);
    if (!url) continue;
    const local = glbXZBounds(publicDir, url, issues, label);
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
    return {
      id: makeId('go'),
      label,
      minX: world.minX,
      maxX: world.maxX,
      minZ: world.minZ,
      maxZ: world.maxZ,
      kind: 'gameobject',
    };
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

/**
 * Walk expanded world tree; collect solid XZ footprints (skip Pad/Road ground).
 */
export function collectFootprints(
  root: ParsedElement,
  publicDir: string,
  issues: AnalyzeIssue[]
): Footprint[] {
  _fpSeq = 0;
  const out: Footprint[] = [];

  const walk = (el: ParsedElement, groupPath: string) => {
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
      for (const c of el.children) {
        const ct = c.tagName.toLowerCase();
        if (!SOLID_PRIMS.has(ct) && ct !== 'pad' && ct !== 'plane') {
          walk(c, nextPath);
        }
      }
      return;
    }

    if (tag === 'gameobject') {
      const fp = gameObjectFootprint(el, nextPath, publicDir, issues);
      if (fp) out.push(fp);
      return;
    }

    // Road / Pad alone are ground — skip
    if (tag === 'road' || tag === 'pad') return;

    for (const c of el.children) walk(c, nextPath);
  };

  walk(root, '');
  return out;
}
