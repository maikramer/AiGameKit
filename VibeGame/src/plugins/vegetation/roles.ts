export type VegetationRole = 'grass' | 'flower' | 'plant';

/**
 * Classify a vegetation GLB URL by filename convention.
 * Override map (from `mesh-roles`) wins when present.
 */
export function classifyVegetationRole(
  url: string,
  overrides?: ReadonlyMap<string, VegetationRole>
): VegetationRole {
  const key = url.trim();
  if (overrides?.has(key)) return overrides.get(key)!;

  const base = key.split('/').pop() ?? key;
  const name = base.replace(/\.(glb|gltf)$/i, '').toLowerCase();

  if (name.startsWith('flower') || name.includes('flower')) return 'flower';
  if (name.startsWith('grass') || name.includes('grass')) return 'grass';
  if (
    name.startsWith('plant') ||
    name.includes('plant') ||
    name.includes('fern') ||
    name.includes('weed')
  ) {
    return 'plant';
  }
  // Unknown carpet props → plant (low density companion).
  return 'plant';
}

/**
 * Parse `mesh-roles="url:grass,/other.glb:flower"` (comma-separated pairs).
 */
export function parseMeshRoleOverrides(
  raw: string | undefined | null
): Map<string, VegetationRole> {
  const out = new Map<string, VegetationRole>();
  if (raw === undefined || raw === null) return out;
  const s = String(raw).trim();
  if (!s) return out;
  for (const part of s.split(',')) {
    const chunk = part.trim();
    if (!chunk) continue;
    const colon = chunk.lastIndexOf(':');
    if (colon <= 0) continue;
    const url = chunk.slice(0, colon).trim();
    const role = chunk
      .slice(colon + 1)
      .trim()
      .toLowerCase();
    if (!url) continue;
    if (role === 'grass' || role === 'flower' || role === 'plant') {
      out.set(url, role);
    }
  }
  return out;
}
