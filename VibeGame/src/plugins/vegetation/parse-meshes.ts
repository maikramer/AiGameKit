/**
 * Parse helpers for `<Vegetation meshes="…">` — kept pure for unit tests.
 */

/** Split a space/comma-separated mesh list into trimmed non-empty URLs. */
export function parseVegetationMeshes(raw: unknown): string[] {
  if (raw == null) return [];
  if (Array.isArray(raw)) {
    return raw
      .map((v) => String(v).trim())
      .filter((s) => s.length > 0 && s !== ',');
  }
  const s = String(raw).trim();
  if (!s) return [];
  return s
    .split(/[\s,]+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
}

export function toBoolAttr(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    const s = value.trim().toLowerCase();
    if (s === '') return fallback;
    if (s === '1' || s === 'true' || s === 'yes') return true;
    if (s === '0' || s === 'false' || s === 'no') return false;
  }
  return fallback;
}
