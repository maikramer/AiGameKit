import { readFile } from 'node:fs/promises';
import * as path from 'node:path';
import { JSDOM } from 'jsdom';
import { expandIncludes, XMLParser } from '../../core/xml';
import { checkAssetUrls } from './assets';
import { expandCityGridsInTree } from './expand-city';
import { collectFootprints } from './footprints';
import { findSolidOverlaps } from './overlap';
import {
  checkCityChildrenOutsideGrid,
  checkCommaCellCoords,
} from './parse-checks';
import type { AnalyzeOptions, AnalyzeResult, AnalyzeIssue } from './types';

let domReady = false;

function ensureDom(): void {
  if (domReady) return;
  if (typeof DOMParser === 'undefined') {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    (globalThis as unknown as { DOMParser: typeof DOMParser }).DOMParser =
      dom.window.DOMParser;
  }
  domReady = true;
}

function extractWorldBody(content: string): string {
  const worldMatch = content.match(/<world\b[^>]*>[\s\S]*?<\/world\s*>/i);
  if (worldMatch) return worldMatch[0]!;
  const sceneMatch = content.match(/<scene\b[^>]*>([\s\S]*?)<\/scene\s*>/i);
  if (sceneMatch) return `<world>${sceneMatch[1]!}</world>`;
  return content.includes('<world') ? content : `<world>${content}</world>`;
}

function resolveInclude(publicDir: string, src: string): string {
  if (src.startsWith('/')) {
    return path.join(publicDir, src.replace(/^\//, ''));
  }
  return path.resolve(publicDir, src);
}

export async function analyzeWorld(
  options: AnalyzeOptions
): Promise<AnalyzeResult> {
  ensureDom();
  const entry = path.resolve(options.entry);
  const publicDir = path.resolve(options.publicDir);
  const issues: AnalyzeIssue[] = [];

  let content: string;
  try {
    content = await readFile(entry, 'utf-8');
  } catch (e) {
    return {
      entry,
      publicDir,
      footprintCount: 0,
      issues: [
        {
          severity: 'error',
          code: 'include',
          message: `[analyze] ERROR cannot read entry: ${entry} (${e instanceof Error ? e.message : e})`,
        },
      ],
      errorCount: 1,
      warnCount: 0,
    };
  }

  const body = extractWorldBody(content);
  let expanded: string;
  try {
    expanded = await expandIncludes(body, {
      load: async (src) => {
        const file = resolveInclude(publicDir, src);
        try {
          return await readFile(file, 'utf-8');
        } catch {
          throw new Error(`[Include] failed to load src="${src}" → ${file}`);
        }
      },
    });
  } catch (e) {
    issues.push({
      severity: 'error',
      code: 'include',
      message: e instanceof Error ? e.message : String(e),
    });
    return finish(entry, publicDir, 0, issues);
  }

  issues.push(...checkCommaCellCoords(expanded));

  let root;
  try {
    const wrapped = expanded.includes('<world')
      ? expanded
      : `<world>${expanded}</world>`;
    root = XMLParser.parse(wrapped).root;
  } catch (e) {
    issues.push({
      severity: 'error',
      code: 'parse',
      message: `[analyze] ERROR invalid XML: ${e instanceof Error ? e.message : e}`,
    });
    return finish(entry, publicDir, 0, issues);
  }

  issues.push(...checkCityChildrenOutsideGrid(root));

  const expandedTree = expandCityGridsInTree(root, issues);
  issues.push(...checkAssetUrls(expandedTree, publicDir));
  const footprints = collectFootprints(expandedTree, publicDir, issues);
  issues.push(...findSolidOverlaps(footprints));

  return finish(entry, publicDir, footprints.length, issues);
}

function finish(
  entry: string,
  publicDir: string,
  footprintCount: number,
  issues: AnalyzeIssue[]
): AnalyzeResult {
  let errorCount = 0;
  let warnCount = 0;
  for (const i of issues) {
    if (i.severity === 'error') errorCount++;
    else if (i.severity === 'warn') warnCount++;
  }
  return {
    entry,
    publicDir,
    footprintCount,
    issues,
    errorCount,
    warnCount,
  };
}
