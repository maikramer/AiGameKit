import type { SpawnTemplateSpec } from './types';

/**
 * Visual GLB URL for a spawn template: direct `url` on the template, or the
 * first `GLTFLoader` / `GLTFDynamic` child (GameObject wrappers).
 *
 * Without the child walk, `ground-align=aabb` and footprint radii silently
 * miss every enemy/NPC that puts the mesh on a child — halfWidth falls back
 * to 0.5 and AABB lift never runs.
 */
export function templateVisualUrl(tpl: SpawnTemplateSpec): string {
  const direct = tpl.attributes.url;
  if (typeof direct === 'string' && direct.trim()) return direct.trim();
  for (const child of tpl.entityChildren ?? []) {
    const tn = String(child.tagName ?? '').toLowerCase();
    if (tn !== 'gltfloader' && tn !== 'gltfdynamic' && tn !== 'gltf-load') {
      continue;
    }
    const u = child.attributes?.url;
    if (typeof u === 'string' && u.trim()) return u.trim();
  }
  return '';
}
