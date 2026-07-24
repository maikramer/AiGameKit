import type { XMLValue } from '../../core';

export function attrString(value: XMLValue | undefined): string | null {
  if (value === undefined || value === null) return null;
  const s = String(value).trim();
  return s === '' ? null : s;
}

export function attrNumber(
  value: XMLValue | undefined,
  fallback: number
): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const n = parseFloat(String(value));
  return Number.isFinite(n) ? n : fallback;
}

/** `origin="x z"` world metres for cell (0,0). */
export function parseOrigin(value: XMLValue | undefined): [number, number] {
  if (value === undefined || value === null) return [0, 0];
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const v = value as { x?: number; y?: number; z?: number };
    if (
      typeof v.x === 'number' &&
      typeof v.y === 'number' &&
      v.z === undefined
    ) {
      return [v.x, v.y];
    }
    if (typeof v.x === 'number' && typeof v.z === 'number') {
      return [v.x, v.z];
    }
  }
  const parts = String(value)
    .trim()
    .split(/[\s,]+/)
    .map(Number)
    .filter((n) => Number.isFinite(n));
  if (parts.length >= 2) return [parts[0]!, parts[1]!];
  return [0, 0];
}

/**
 * Cell coord `at="2 1"` → [cx, cz].
 * Prefer space-separated values (`"2 1"` → `{x,y}`). Commas become a number.
 */
export function parseCell(
  value: XMLValue | undefined,
  label: string
): [number, number] {
  if (value === undefined || value === null) {
    throw new Error(`[CityLayout] ${label} requires cell coords (e.g. "2 1")`);
  }
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const v = value as { x?: number; y?: number; z?: number };
    if (
      typeof v.x === 'number' &&
      typeof v.y === 'number' &&
      v.z === undefined
    ) {
      return [v.x, v.y];
    }
    if (typeof v.x === 'number' && typeof v.z === 'number') {
      return [v.x, v.z];
    }
  }
  if (typeof value === 'number') {
    throw new Error(
      `[CityLayout] ${label} got a single number (${value}). ` +
        'Use space-separated cells: at="2 1".'
    );
  }
  const parts = String(value)
    .trim()
    .split(/[\s,]+/)
    .map(Number)
    .filter((n) => Number.isFinite(n));
  if (parts.length < 2) {
    throw new Error(
      `[CityLayout] ${label} must be "cx cz" (got ${String(value)})`
    );
  }
  return [parts[0]!, parts[1]!];
}

export function cellToWorld(
  cx: number,
  cz: number,
  cell: number,
  originX: number,
  originZ: number
): [number, number] {
  return [originX + cx * cell, originZ + cz * cell];
}

/** Axis-aligned cell rect via `min`+`max` or `from`+`to`. */
export function parseCellRect(
  attrs: Record<string, import('../../core').XMLValue | undefined>,
  label: string
): { minX: number; minZ: number; maxX: number; maxZ: number } {
  const minRaw = attrs.min ?? attrs.from;
  const maxRaw = attrs.max ?? attrs.to;
  if (minRaw === undefined || maxRaw === undefined) {
    throw new Error(
      `[CityLayout] ${label} requires min/max or from/to cell coords`
    );
  }
  const [a, b] = parseCell(minRaw, `${label} min/from`);
  const [c, d] = parseCell(maxRaw, `${label} max/to`);
  return {
    minX: Math.min(a, c),
    minZ: Math.min(b, d),
    maxX: Math.max(a, c),
    maxZ: Math.max(b, d),
  };
}

export function parseGateSides(raw: unknown): Set<string> {
  const out = new Set<string>();
  if (raw === undefined || raw === null) return out;
  const s = String(raw).trim().toLowerCase();
  if (s === '' || s === 'none') return out;
  for (const part of s.split(/[\s,|]+/)) {
    if (part === 'n' || part === 'north') out.add('n');
    else if (part === 'e' || part === 'east') out.add('e');
    else if (part === 's' || part === 'south') out.add('s');
    else if (part === 'w' || part === 'west') out.add('w');
  }
  return out;
}
