import type { ParsedElement } from '../../core';
import type { AnalyzeIssue } from './types';

const CITY_CHILD_TAGS = new Set([
  'street',
  'streetring',
  'streetcross',
  'building',
  'buildingrow',
  'block',
  'wall',
  'wallrect',
  'plaza',
  'prop',
  'slot',
  'gate',
]);

/** Scan raw XML for comma-separated cell coords (parser turns commas into numbers). */
export function checkCommaCellCoords(xml: string): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  const re = /\b(at|from|to|min|max|origin)\s*=\s*"([^"]*,[^"]*)"/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(xml)) !== null) {
    issues.push({
      severity: 'error',
      code: 'parse',
      message:
        `[analyze] ERROR ${m[1]}="${m[2]}" — use space-separated cell coords ` +
        `(${m[1]}="2 1"), not commas`,
    });
  }
  return issues;
}

/** CityGrid child recipes used outside CityGrid. */
export function checkCityChildrenOutsideGrid(
  root: ParsedElement
): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];

  const walk = (el: ParsedElement, insideGrid: boolean) => {
    const tag = el.tagName.toLowerCase();
    if (CITY_CHILD_TAGS.has(tag) && !insideGrid) {
      issues.push({
        severity: 'error',
        code: 'parse',
        message: `[analyze] ERROR <${el.tagName}> must be a child of <CityGrid>`,
      });
    }
    const childInside = tag === 'citygrid' ? true : insideGrid;
    for (const c of el.children) walk(c, childInside);
  };

  walk(root, false);
  return issues;
}
