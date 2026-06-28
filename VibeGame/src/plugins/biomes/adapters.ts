import type { Adapter } from '../../core';
import { BiomeRegion } from './components';

/** A single parsed polygon: vertex list (XZ pairs) plus its AABB. */
export interface PolygonGeometry {
  vertices: number[][];
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

/**
 * Parse a `polygon` attribute. Supports two formats:
 * - Bracket: `"[x1,z1;x2,z2;...]"` (preferred — the `[` prefix prevents the
 *   engine XML parser from auto-converting the value to a number).
 * - Legacy: `"x1 z1, x2 z2, ..."` (whitespace-tolerant, comma-separated pairs).
 *
 * Returns the vertex list and the bounding box. An empty or unparsable string
 * yields an empty polygon (degenerate AABB) so detection simply never matches.
 */
export function parsePolygonString(value: string): PolygonGeometry {
  const vertices: number[][] = [];
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  const raw = String(value ?? '').trim();
  if (raw !== '') {
    let pairs: string[];
    if (raw.startsWith('[')) {
      const inner = raw.replace(/^\[/, '').replace(/\]$/, '');
      pairs = inner.split(';');
    } else {
      pairs = raw.split(',');
    }
    for (const pair of pairs) {
      const sep = pair.trim().includes(',') ? ',' : /\s+/;
      const parts = pair.trim().split(sep);
      if (parts.length < 2) continue;
      const x = parseFloat(parts[0] as string);
      const z = parseFloat(parts[1] as string);
      if (Number.isNaN(x) || Number.isNaN(z)) continue;
      vertices.push([x, z]);
      if (x < minX) minX = x;
      if (z < minZ) minZ = z;
      if (x > maxX) maxX = x;
      if (z > maxZ) maxZ = z;
    }
  }
  if (vertices.length === 0) {
    minX = 0;
    minZ = 0;
    maxX = 0;
    maxZ = 0;
  }
  return { vertices, minX, minZ, maxX, maxZ };
}

export function aabbContains(
  minX: number,
  minZ: number,
  maxX: number,
  maxZ: number,
  x: number,
  z: number
): boolean {
  return x >= minX && x <= maxX && z >= minZ && z <= maxZ;
}

/**
 * Point-in-polygon via the ray-casting (even-odd) algorithm. `vertices` is a
 * list of `[x, z]` pairs. Points exactly on an edge are treated as inside.
 * A polygon with fewer than 3 vertices never contains anything.
 */
export function pointInPolygon(
  x: number,
  z: number,
  vertices: number[][]
): boolean {
  const n = vertices.length;
  if (n < 3) return false;
  let inside = false;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = vertices[i]![0];
    const zi = vertices[i]![1];
    const xj = vertices[j]![0];
    const zj = vertices[j]![1];
    const intersects =
      zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

/** Parse a color attribute: `"#RRGGBB"` hex or `"r g b"` floats (0..1 / 0..255). */
export function parseColor(value: string | number): {
  r: number;
  g: number;
  b: number;
} {
  const s = String(value).trim();
  const asNum = Number(s);
  if (Number.isFinite(asNum) && s !== '' && !s.startsWith('0.')) {
    const n = Math.floor(asNum);
    return {
      r: ((n >> 16) & 0xff) / 255,
      g: ((n >> 8) & 0xff) / 255,
      b: (n & 0xff) / 255,
    };
  }
  if (s.startsWith('#')) {
    const num = parseInt(s.slice(1), 16);
    if (Number.isNaN(num)) return { r: 1, g: 1, b: 1 };
    return {
      r: ((num >> 16) & 0xff) / 255,
      g: ((num >> 8) & 0xff) / 255,
      b: (num & 0xff) / 255,
    };
  }
  const parts = s.split(/\s+/).map((p) => parseFloat(p));
  if (parts.length >= 3 && parts.every((n) => !Number.isNaN(n))) {
    const anyAboveOne = parts.some((n) => n > 1);
    const scale = anyAboveOne ? 1 / 255 : 1;
    return {
      r: parts[0]! * scale,
      g: parts[1]! * scale,
      b: parts[2]! * scale,
    };
  }
  return { r: 1, g: 1, b: 1 };
}

/** Pack linear 0..1 RGB into a 0xRRGGBB uint32 (Postprocessing.fogColor convention). */
export function packRgb(r: number, g: number, b: number): number {
  const ri = Math.round(clamp01(r) * 255);
  const gi = Math.round(clamp01(g) * 255);
  const bi = Math.round(clamp01(b) * 255);
  return ((ri << 16) | (gi << 8) | bi) >>> 0;
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Writes the AABB derived from a `polygon` attribute onto a BiomeRegion entity. */
export const polygonAdapter: Adapter = (entity, value) => {
  const g = parsePolygonString(String(value));
  BiomeRegion.polyMinX[entity] = g.minX;
  BiomeRegion.polyMinZ[entity] = g.minZ;
  BiomeRegion.polyMaxX[entity] = g.maxX;
  BiomeRegion.polyMaxZ[entity] = g.maxZ;
};

export const tintAdapter: Adapter = (entity, value) => {
  const { r, g, b } = parseColor(String(value));
  BiomeRegion.tintR[entity] = r;
  BiomeRegion.tintG[entity] = g;
  BiomeRegion.tintB[entity] = b;
};

export const ambientAdapter: Adapter = (entity, value) => {
  const { r, g, b } = parseColor(String(value));
  BiomeRegion.ambientR[entity] = r;
  BiomeRegion.ambientG[entity] = g;
  BiomeRegion.ambientB[entity] = b;
};

export const fogColorAdapter: Adapter = (entity, value) => {
  const { r, g, b } = parseColor(String(value));
  BiomeRegion.fogColor[entity] = packRgb(r, g, b);
};

export const fogDensityAdapter: Adapter = (entity, value) => {
  const n = parseFloat(String(value));
  if (!Number.isNaN(n) && n >= 0) BiomeRegion.fogDensity[entity] = n;
};

export const typeAdapter: Adapter = (entity, value) => {
  const n = parseInt(String(value), 10);
  if (!Number.isNaN(n)) BiomeRegion.type[entity] = n;
};

export const bgmLayerAdapter: Adapter = (entity, value) => {
  const n = parseInt(String(value), 10);
  if (!Number.isNaN(n)) BiomeRegion.bgmLayer[entity] = n;
};
