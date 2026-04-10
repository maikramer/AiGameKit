import type { XMLValue } from '../../core';
import { resolveGroupSpawnFields } from './profiles';
import type { SpawnGroupProfileId } from './profiles';

/** Parse `at` (two numbers: world X Z). */
export function parseAt(value: XMLValue | undefined): [number, number] {
  if (value === undefined || value === null) {
    throw new Error(
      '[entity] Missing "at" in place string.\n' +
        '  Example: place="at: 0 -8; base-y-offset: 0.02"'
    );
  }
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    const o = value as Record<string, number>;
    if ('x' in o && 'z' in o) {
      return [Number(o.x), Number(o.z)];
    }
    if ('x' in o && 'y' in o) {
      return [Number(o.x), Number(o.y)];
    }
  }
  if (Array.isArray(value) && value.length >= 2) {
    return [Number(value[0]), Number(value[1])];
  }
  if (typeof value === 'string') {
    const p = value
      .trim()
      .split(/\s+/)
      .map((x) => parseFloat(x));
    if (p.length >= 2 && p.every((n) => !Number.isNaN(n))) {
      return [p[0]!, p[1]!];
    }
  }
  if (typeof value === 'number') {
    return [value, 0];
  }
  throw new Error(
    '[entity] "at" must be two numbers: "x z" (e.g. at: 12 -4).'
  );
}

/**
 * Parse a semicolon-separated `key: value` string (entity `place` attribute).
 */
export function parseSemicolonPlaceString(placeStr: string): Record<string, XMLValue> {
  const out: Record<string, XMLValue> = {};
  for (const part of placeStr.split(';')) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const colon = trimmed.indexOf(':');
    if (colon === -1) continue;
    const rawKey = trimmed.slice(0, colon).trim();
    const key = rawKey.replace(/\s+/g, '-').toLowerCase();
    const val = trimmed.slice(colon + 1).trim();
    out[key] = val;
  }
  return out;
}

export function resolveSpawnFromPlaceAttrs(
  mergedAttrs: Record<string, XMLValue>,
  profileId: SpawnGroupProfileId
) {
  if ('y-offset' in mergedAttrs && !('base-y-offset' in mergedAttrs)) {
    mergedAttrs['base-y-offset'] = mergedAttrs['y-offset'];
  }
  return resolveGroupSpawnFields(mergedAttrs, profileId);
}
