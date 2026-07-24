import { existsSync } from 'node:fs';
import * as path from 'node:path';
import type { ParsedElement, XMLValue } from '../../core';
import type { AnalyzeIssue } from './types';

const URL_ATTRS = [
  'url',
  'lod1-url',
  'lod2-url',
  'texture-url',
  'map-url',
  'normal-map-url',
  'roughness-map-url',
  'mesh-url',
] as const;

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

function isLodSecondary(attr: string): boolean {
  return attr === 'lod1-url' || attr === 'lod2-url';
}

/**
 * Walk tree; report missing `/assets/…` (and other site-root) files under publicDir.
 */
export function checkAssetUrls(
  root: ParsedElement,
  publicDir: string
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const seen = new Set<string>();

  const checkUrl = (url: string, attr: string, tag: string) => {
    if (!url.startsWith('/')) return;
    const key = `${attr}:${url}`;
    if (seen.has(key)) return;
    seen.add(key);
    const file = path.join(publicDir, url.replace(/^\//, ''));
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
