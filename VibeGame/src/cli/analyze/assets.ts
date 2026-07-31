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

/** Resolve site-root (`/…`) or relative path under publicDir. */
export function resolveAssetPath(
  publicDir: string,
  url: string
): string | null {
  const t = url.trim();
  if (!t || isRemoteUrl(t)) return null;
  if (t.startsWith('/')) {
    return path.join(publicDir, t.replace(/^\//, ''));
  }
  return path.join(publicDir, t);
}

/**
 * Walk tree; report missing asset files under publicDir (absolute `/…` or relative).
 */
export function checkAssetUrls(
  root: ParsedElement,
  publicDir: string
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const seen = new Set<string>();

  const checkUrl = (url: string, attr: string, tag: string) => {
    if (isRemoteUrl(url)) return;
    const key = `${attr}:${url}`;
    if (seen.has(key)) return;
    seen.add(key);
    const file = resolveAssetPath(publicDir, url);
    if (!file) return;
    if (existsSync(file)) return;
    const lod = isLodSecondary(attr);
    issues.push({
      severity: lod ? 'warn' : 'error',
      code: 'asset',
      message: `[analyze] ${lod ? 'WARN' : 'ERROR'} missing asset ${url} (<${tag} ${attr}>)`,
    });
  };

  const walk = (el: ParsedElement) => {
    const tag = el.tagName;
    for (const attr of URL_ATTRS) {
      const url = attrStr(el.attributes[attr]);
      if (url) checkUrl(url, attr, tag);
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
    for (const c of el.children) walk(c);
  };

  walk(root);
  return issues;
}
