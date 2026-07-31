import type { ParsedElement, XMLValue } from '../../core';
import type { AnalyzeIssue } from './types';

const SPAWNER_TAGS = new Set([
  'staticspawner',
  'dynamicspawner',
  'spawngroup',
  'spawn-group',
]);

const PLAYER_TAGS = new Set(['player', 'playergltf']);
const CAMERA_TAGS = new Set(['orbitcamera', 'thirdpersoncamera']);

function attrStr(v: XMLValue | undefined): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v === 'string') {
    const t = v.trim();
    return t === '' ? null : t;
  }
  if (typeof v === 'number') return String(v);
  return null;
}

function hasSpawnCount(el: ParsedElement): boolean {
  const density = attrStr(el.attributes['density-per-km2']);
  if (density) return true;
  const cmin = attrStr(el.attributes['count-min']);
  const cmax = attrStr(el.attributes['count-max']);
  if (cmin && cmax) return true;
  const count = attrStr(el.attributes.count);
  if (count) {
    const n = parseFloat(count);
    return Number.isFinite(n) && n >= 1;
  }
  return false;
}

/**
 * Spawner empty/count, missing player/camera, duplicate names, terrain heightmap.
 */
export function checkWorld(root: ParsedElement): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  let playerCount = 0;
  let cameraCount = 0;
  const names = new Map<string, number>();

  const walk = (el: ParsedElement) => {
    const tagLower = el.tagName.toLowerCase();

    if (SPAWNER_TAGS.has(tagLower)) {
      const kids = el.children.filter(
        (c) => c.tagName && c.tagName.toLowerCase() !== 'parsererror'
      );
      if (kids.length === 0) {
        issues.push({
          severity: 'error',
          code: 'spawner',
          message: `[analyze] ERROR <${el.tagName}> has no child template`,
        });
      }
      if (!hasSpawnCount(el)) {
        issues.push({
          severity: 'error',
          code: 'spawner',
          message: `[analyze] ERROR <${el.tagName}> needs count≥1, density-per-km2, or count-min+count-max`,
        });
      }
    }

    if (PLAYER_TAGS.has(tagLower)) playerCount += 1;
    if (CAMERA_TAGS.has(tagLower)) cameraCount += 1;

    if (tagLower === 'terrain') {
      const hm =
        attrStr(el.attributes.heightmap) ??
        attrStr(el.attributes['heightmap-url']);
      if (!hm) {
        issues.push({
          severity: 'warn',
          code: 'world',
          message:
            '[analyze] WARN <Terrain> has no heightmap — procedural heightfield will be used',
        });
      }
    }

    const name = attrStr(el.attributes.name);
    if (name) {
      names.set(name, (names.get(name) ?? 0) + 1);
    }

    for (const c of el.children) walk(c);
  };

  walk(root);

  if (playerCount === 0) {
    issues.push({
      severity: 'warn',
      code: 'world',
      message:
        '[analyze] WARN no <Player> / <PlayerGLTF> in XML (startup may auto-create)',
    });
  }
  if (cameraCount === 0) {
    issues.push({
      severity: 'warn',
      code: 'world',
      message:
        '[analyze] WARN no <OrbitCamera> / <ThirdPersonCamera> in XML (startup may auto-create)',
    });
  }

  for (const [name, count] of names) {
    if (count < 2) continue;
    issues.push({
      severity: 'warn',
      code: 'name',
      message: `[analyze] WARN duplicate name="${name}" (${count}×) — runtime overwrites`,
    });
  }

  return issues;
}
