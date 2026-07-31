import type { XMLValue } from './types';

const COLOR_MAP: Record<string, number> = {
  red: 0xff0000,
  green: 0x00ff00,
  blue: 0x0000ff,
  yellow: 0xffff00,
  purple: 0xff00ff,
  cyan: 0x00ffff,
  white: 0xffffff,
  black: 0x000000,
  gray: 0x808080,
  orange: 0xffa500,
  pink: 0xffc0cb,
  lime: 0x00ff00,
  gold: 0xffd700,
};

const VECTOR_PATTERN = /^-?\d+(\.\d+)?(\s+-?\d+(\.\d+)?)+$/;

export const XMLValueParser = {
  parse(value: string): XMLValue {
    if (this.isVector(value)) return this.parseVector(value);
    if (this.isHexColor(value)) return this.parseHexColor(value);
    if (this.isNamedColor(value)) return this.parseNamedColor(value);
    if (this.isBoolean(value)) return this.parseBoolean(value);
    if (this.isNumber(value)) return this.parseNumber(value);
    return value;
  },

  isVector(value: string): boolean {
    return VECTOR_PATTERN.test(value);
  },

  parseVector(value: string): Record<string, number> | number[] {
    const parts = value.split(/\s+/).map(Number);
    if (parts.length === 2) return { x: parts[0], y: parts[1] };
    if (parts.length === 3) return { x: parts[0], y: parts[1], z: parts[2] };
    if (parts.length === 4)
      return { x: parts[0], y: parts[1], z: parts[2], w: parts[3] };
    return parts;
  },

  isHexColor(value: string): boolean {
    if (value.startsWith('0x')) {
      return /^0x[0-9a-fA-F]+$/.test(value);
    }
    if (value.startsWith('#')) {
      return /^#[0-9a-fA-F]+$/.test(value);
    }
    return false;
  },

  parseHexColor(value: string): number {
    if (value.startsWith('0x')) {
      return parseInt(value, 16);
    }
    return parseInt(value.slice(1), 16);
  },

  isNamedColor(value: string): boolean {
    return Object.prototype.hasOwnProperty.call(COLOR_MAP, value.toLowerCase());
  },

  parseNamedColor(value: string): number {
    return COLOR_MAP[value.toLowerCase()];
  },

  isBoolean(value: string): boolean {
    return value === 'true' || value === 'false';
  },

  parseBoolean(value: string): boolean {
    return value === 'true';
  },

  isNumber(value: string): boolean {
    return !isNaN(parseFloat(value));
  },

  parseNumber(value: string): number {
    return parseFloat(value);
  },
};

/**
 * Parse a numeric XML attribute with a fallback. Values may arrive as
 * numbers/booleans (XMLValueParser pre-conversion), strings, or missing —
 * NaN and non-numeric strings fall back. Canonical home for the per-plugin
 * `toNumber`/`toNum`/`toFloat`/`attrNumber` copies.
 */
export function parseNumberAttr(value: unknown, fallback: number): number {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'number') return Number.isNaN(value) ? fallback : value;
  if (typeof value === 'boolean') return value ? 1 : 0;
  const n = parseFloat(String(value).trim());
  return Number.isNaN(n) ? fallback : n;
}

/**
 * Parse a boolean XML attribute. Accepts `true/false`, `1/0`, `yes/no`,
 * `on/off` (case-insensitive); anything unrecognized falls back. Unlike the
 * ad-hoc per-plugin copies, `"false"`/`"no"`/`"0"`/`"off"` are honored
 * (sky's old copy returned the fallback for any unrecognized string, so
 * `"false"` became `true`).
 */
export function parseBoolAttr(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  const s = String(value).trim().toLowerCase();
  if (s === 'true' || s === '1' || s === 'yes' || s === 'on') return true;
  if (s === 'false' || s === '0' || s === 'no' || s === 'off') return false;
  return fallback;
}

/**
 * Parse a vec3 XML attribute into a `[x, y, z]` tuple with a per-axis
 * fallback. Accepts every shape XMLValueParser can produce: `"x y z"`
 * strings, bare numbers (broadcast), arrays, and `{x,y,z}` objects (z falls
 * back to `w` for 4-component vectors). Canonical home for the per-plugin
 * `vec3FromAttr`/`parseVec3` copies.
 */
export function parseVec3Attr(
  value: unknown,
  fallback: [number, number, number]
): [number, number, number] {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number') {
    return Number.isNaN(value) ? fallback : [value, value, value];
  }
  if (typeof value === 'string') {
    const parts = value.trim().split(/\s+/).map(parseFloat);
    if (parts.length === 1 && !Number.isNaN(parts[0])) {
      return [parts[0], parts[0], parts[0]];
    }
    if (parts.length >= 3 && parts.slice(0, 3).every((n) => !Number.isNaN(n))) {
      return [parts[0], parts[1], parts[2]];
    }
    return fallback;
  }
  if (Array.isArray(value) && value.length >= 3) {
    const n = [Number(value[0]), Number(value[1]), Number(value[2])];
    if (n.every((x) => !Number.isNaN(x))) return [n[0], n[1], n[2]];
    return fallback;
  }
  if (typeof value === 'object' && value !== null) {
    const v = value as Record<string, unknown>;
    const x = parseNumberAttr(v.x, Number.NaN);
    const y = parseNumberAttr(v.y, Number.NaN);
    const z = parseNumberAttr(v.z ?? v.w, Number.NaN);
    if (!Number.isNaN(x) && !Number.isNaN(y) && !Number.isNaN(z)) {
      return [x, y, z];
    }
  }
  return fallback;
}

/**
 * Parse a color attribute that may have been pre-converted by XMLValueParser
 * (`#hex` / `0xhex` → number, then stringified by the adapter layer). A pure
 * decimal digit string is the round-trip of that conversion (the canonical
 * `#hex` form in world XML); anything else is parsed as hex (optional
 * `#`/`0x` prefix). Returns NaN for garbage.
 *
 * Note: a bare all-digit hex string like `"888888"` (no `#`/`0x` prefix) is
 * indistinguishable from a converted round-trip and is treated as decimal.
 * Prefix colors with `#` to be unambiguous — that is also the form
 * XMLValueParser normalizes to numbers anyway.
 */
export function parseColorValue(value: string): number {
  const s = value.trim();
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  return parseInt(s.replace(/^#/, '').replace(/^0x/i, ''), 16);
}

/**
 * Split a whitespace-separated token list (`"a b c"` → `["a","b","c"]`).
 * Non-strings and empty input yield `[]`. Canonical home for the repeated
 * `String(x).trim().split(/\s+/).filter(Boolean)` idiom.
 */
export function splitTokens(value: unknown): string[] {
  if (typeof value !== 'string') return [];
  return value.trim().split(/\s+/).filter(Boolean);
}

/**
 * Split a whitespace-separated number list (`"1 2 3"` → `[1,2,3]`).
 * Non-numeric tokens are dropped; non-strings and empty input yield `[]`.
 */
export function splitNumbers(value: unknown): number[] {
  return splitTokens(value)
    .map(Number)
    .filter((n) => !Number.isNaN(n));
}

/**
 * Flatten a numeric-list attribute in any shape XMLValueParser can produce:
 * `"x z …"` strings, `number[]`, or `{x,y}` / `{x,y,z}` / `{x,y,z,w}`
 * objects (2- and 4-number lists are pre-converted to those object shapes).
 */
export function flattenNumberList(value: unknown): number[] {
  if (Array.isArray(value)) {
    return value.map(Number).filter((n) => Number.isFinite(n));
  }
  if (typeof value === 'string') {
    return splitNumbers(value);
  }
  if (typeof value === 'object' && value !== null) {
    const o = value as Record<string, unknown>;
    const vals: number[] = [];
    for (const k of ['x', 'y', 'z', 'w'] as const) {
      const v = o[k];
      if (typeof v !== 'number' || !Number.isFinite(v)) break;
      vals.push(v);
    }
    if (vals.length >= 2) return vals;
  }
  return [];
}
