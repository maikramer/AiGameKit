import { existsSync } from 'node:fs';
import * as path from 'node:path';
import type { ParsedElement, XMLValue } from '../../core';
import type { AnalyzeIssue } from './types';

/**
 * Site-root asset attrs used across recipes (GLB + images/textures).
 * Keep in sync with plugins that accept `/…` file paths (terrain, HUD,
 * PlayerGLTF, vegetation hubs, road/PBR maps).
 */
const URL_ATTRS = [
  'url',
  'model-url',
  'lod1-url',
  'lod2-url',
  'texture-url',
  'map-url',
  'normal-map-url',
  'roughness-map-url',
  'mesh-url',
  // Images / maps (were silently skipped — only GLB attrs were checked)
  'heightmap',
  'heightmap-url',
  'texture',
  'terrain-texture',
  'icon',
  'src',
] as const;

/** Space-separated lists of `/assets/…` paths (vegetation `meshes="… …"`). */
const URL_LIST_ATTRS = ['meshes'] as const;

function attrStr(v: XMLValue | undefined): string | null {
  if (typeof v === 'string' && v.trim() !== '') return v.trim();
  return null;
}

function urlsFromCollider(collider: string): string[] {
  const out: string[] = [];
  const m = collider.match(/mesh-url\s*:\s*([^;]+)/i);
  if (m) out.push(m[1]!.trim());
  return out;
}

function urlsFromList(raw: string): string[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0 && !/^https?:\/\//i.test(s));
}

function isLodSecondary(attr: string): boolean {
  return attr === 'lod1-url' || attr === 'lod2-url';
}

function isRemoteUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/**
 * Find the shared asset pool that serves this example, if there is one.
 *
 * Mirrors what `vibegame({ sharedAssets })` does at runtime: assets live once in
 * `examples/shared-assets/public/assets` and every example reads them from there,
 * so an offline analyze that only looked under the example's own `public/` would
 * report every pooled GLB as missing.
 */
export function sharedAssetRoots(publicDir: string): string[] {
  const roots: string[] = [];
  let dir = path.resolve(publicDir);
  for (let i = 0; i < 6; i++) {
    const parent = path.dirname(dir);
    if (parent === dir) break;
    const pool = path.join(parent, 'shared-assets', 'public');
    if (pool !== path.resolve(publicDir) && existsSync(pool)) roots.push(pool);
    dir = parent;
  }
  return roots;
}

/**
 * Resolve site-root (`/…`) or relative path under publicDir.
 *
 * `extraRoots` are searched when the example itself does not ship the file —
 * the example always wins, exactly like the dev-server middleware.
 */
export function resolveAssetPath(
  publicDir: string,
  url: string,
  extraRoots: readonly string[] = []
): string | null {
  const t = url.trim();
  if (!t || isRemoteUrl(t)) return null;
  const rel = t.startsWith('/') ? t.replace(/^\//, '') : t;
  const primary = path.join(publicDir, rel);
  if (existsSync(primary)) return primary;
  for (const root of extraRoots) {
    const candidate = path.join(root, rel);
    if (existsSync(candidate)) return candidate;
  }
  return primary;
}

/** `foo_lod1.glb` → `foo_lod0.glb` path string, or null if not a lodN (N≥1) name. */
export function lod0SiblingUrl(url: string): string | null {
  const m = url.trim().match(/^(.*_lod)([1-9])(\.glb)(\?.*)?$/i);
  if (!m) return null;
  return `${m[1]}0${m[3]}${m[4] ?? ''}`;
}

/**
 * Walk tree; report missing asset files under publicDir (absolute `/…` or relative).
 * Also warns when `url` aliases `lod1-url` or skips an existing `*_lod0.glb`
 * (InstancedMesh2 near-band vanish / soft LOD ladder).
 */
export function checkAssetUrls(
  root: ParsedElement,
  publicDir: string,
  extraRoots: readonly string[] = sharedAssetRoots(publicDir)
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const seen = new Set<string>();
  const lodLadderSeen = new Set<string>();

  const checkUrl = (url: string, attr: string, tag: string) => {
    if (isRemoteUrl(url)) return;
    const key = `${attr}:${url}`;
    if (seen.has(key)) return;
    seen.add(key);
    const file = resolveAssetPath(publicDir, url, extraRoots);
    if (!file) return;
    if (existsSync(file)) return;
    const lod = isLodSecondary(attr);
    issues.push({
      severity: lod ? 'warn' : 'error',
      code: 'asset',
      message: `[analyze] ${lod ? 'WARN' : 'ERROR'} missing asset ${url} (<${tag} ${attr}>)`,
    });
  };

  const checkLodLadder = (el: ParsedElement) => {
    const tag = el.tagName;
    const url =
      attrStr(el.attributes.url) ?? attrStr(el.attributes['model-url']);
    if (!url || isRemoteUrl(url)) return;
    const lod1 = attrStr(el.attributes['lod1-url']);
    const ladderKey = `${tag}:${url}:${lod1 ?? ''}`;
    if (lodLadderSeen.has(ladderKey)) return;
    lodLadderSeen.add(ladderKey);

    if (lod1 && lod1 === url) {
      issues.push({
        severity: 'warn',
        code: 'asset',
        message:
          `[analyze] WARN url aliases lod1-url (${url}) (<${tag}>) — ` +
          `instanced LOD near-band can vanish; use distinct *_lod0 / *_lod1`,
      });
    }

    const lod0 = lod0SiblingUrl(url);
    if (!lod0) return;
    const lod0Path = resolveAssetPath(publicDir, lod0, extraRoots);
    if (!lod0Path || !existsSync(lod0Path)) return;
    issues.push({
      severity: 'warn',
      code: 'asset',
      message:
        `[analyze] WARN url=${url} skips existing ${lod0} (<${tag}>) — ` +
        `prefer url=*_lod0 for near detail`,
    });
  };

  const walk = (el: ParsedElement) => {
    const tag = el.tagName;
    for (const attr of URL_ATTRS) {
      const url = attrStr(el.attributes[attr]);
      if (!url) continue;
      // HUD `icon` accepts emoji/text as well as paths — same convention as
      // isIconImagePath (shared.ts): only path-like values are assets.
      if (attr === 'icon' && !/\.(png|jpe?g|webp|svg|gif)$/i.test(url))
        continue;
      checkUrl(url, attr, tag);
    }
    for (const attr of URL_LIST_ATTRS) {
      const raw = attrStr(el.attributes[attr]);
      if (!raw) continue;
      for (const url of urlsFromList(raw)) {
        checkUrl(url, attr, tag);
      }
    }
    const collider = attrStr(el.attributes.collider);
    if (collider) {
      for (const url of urlsFromCollider(collider)) {
        checkUrl(url, 'mesh-url', tag);
      }
    }
    checkLodLadder(el);
    for (const c of el.children) walk(c);
  };

  walk(root);
  return issues;
}
