import type { ParsedElement } from '../../core';
import {
  expandBlock,
  expandBuilding,
  expandBuildingRow,
  expandGate,
  expandPlaza,
  expandProp,
  expandSlot,
  expandStreet,
  expandStreetCross,
  expandStreetRing,
  expandWall,
  expandWallRect,
  type GridCtx,
} from '../../plugins/city-layout/expand';
import {
  attrNumber,
  attrString,
  parseOrigin,
} from '../../plugins/city-layout/grid';
import type { AnalyzeIssue } from './types';

type Expander = (
  child: ParsedElement,
  g: GridCtx
) => ParsedElement | ParsedElement[];

const CHILD_EXPANDERS: Record<string, Expander> = {
  street: expandStreet,
  streetring: expandStreetRing,
  streetcross: expandStreetCross,
  building: expandBuilding,
  buildingrow: expandBuildingRow,
  block: expandBlock,
  wall: expandWall,
  wallrect: expandWallRect,
  plaza: expandPlaza,
  prop: expandProp,
  slot: expandSlot,
  gate: expandGate,
};

/**
 * Recursively expand `<CityGrid>` children to Composition/Road/GameObject
 * so footprint walk sees the same world as runtime.
 */
export function expandCityGridsInTree(
  element: ParsedElement,
  issues: AnalyzeIssue[]
): ParsedElement {
  const children = element.children.map((c) =>
    expandCityGridsInTree(c, issues)
  );
  const tag = element.tagName.toLowerCase();
  if (tag !== 'citygrid') {
    return { ...element, children };
  }

  const cell = attrNumber(element.attributes.cell, 4);
  if (!(cell > 0)) {
    issues.push({
      severity: 'error',
      code: 'parse',
      message: '[CityGrid] cell= must be a positive number',
    });
    return { ...element, children: [] };
  }

  let originX = 0;
  let originZ = 0;
  try {
    [originX, originZ] = parseOrigin(element.attributes.origin);
  } catch (e) {
    issues.push({
      severity: 'error',
      code: 'parse',
      message: e instanceof Error ? e.message : String(e),
    });
    return { ...element, children: [] };
  }

  const align = attrString(element.attributes['align-to-terrain']) ?? '0';
  const g: GridCtx = { cell, originX, originZ, align };
  const expanded: ParsedElement[] = [];

  for (const child of children) {
    if (!child.tagName || child.tagName.toLowerCase() === 'parsererror') {
      continue;
    }
    const childTag = child.tagName.toLowerCase();
    const expander = CHILD_EXPANDERS[childTag];
    if (expander) {
      try {
        const result = expander(child, g);
        if (Array.isArray(result)) expanded.push(...result);
        else expanded.push(result);
      } catch (e) {
        issues.push({
          severity: 'error',
          code: 'parse',
          message: e instanceof Error ? e.message : String(e),
        });
      }
      continue;
    }
    // Pass through unknown recipe children (Group, etc.)
    expanded.push(child);
  }

  return {
    tagName: 'Group',
    attributes: {
      ...element.attributes,
      name:
        element.attributes.name ??
        element.attributes['data-name'] ??
        'city.grid',
    },
    children: expanded.map((c) => expandCityGridsInTree(c, issues)),
  };
}
