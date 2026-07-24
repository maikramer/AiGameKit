import type { ParsedElement, Parser } from '../../core';
import { processRecipeChildElements } from '../../core/recipes/parser';
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
} from './expand';
import { attrNumber, attrString, parseOrigin } from './grid';

function requireInsideCityGrid(tag: string): Parser {
  return () => {
    throw new Error(
      `[${tag}] must be a child of <CityGrid> (not used as a top-level recipe)`
    );
  };
}

export const streetParser: Parser = requireInsideCityGrid('Street');
export const streetRingParser: Parser = requireInsideCityGrid('StreetRing');
export const streetCrossParser: Parser = requireInsideCityGrid('StreetCross');
export const buildingParser: Parser = requireInsideCityGrid('Building');
export const buildingRowParser: Parser = requireInsideCityGrid('BuildingRow');
export const blockParser: Parser = requireInsideCityGrid('Block');
export const wallParser: Parser = requireInsideCityGrid('Wall');
export const wallRectParser: Parser = requireInsideCityGrid('WallRect');
export const plazaParser: Parser = requireInsideCityGrid('Plaza');
export const propParser: Parser = requireInsideCityGrid('Prop');
export const slotParser: Parser = requireInsideCityGrid('Slot');
export const gateParser: Parser = requireInsideCityGrid('Gate');

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

export const cityGridParser: Parser = ({ entity, element, state, context }) => {
  const cell = attrNumber(element.attributes.cell, 4);
  if (!(cell > 0)) {
    throw new Error('[CityGrid] cell= must be a positive number');
  }
  const [originX, originZ] = parseOrigin(element.attributes.origin);
  const align = attrString(element.attributes['align-to-terrain']) ?? '0';
  const g: GridCtx = { cell, originX, originZ, align };

  const expanded: ParsedElement[] = [];
  for (const child of element.children) {
    if (!child.tagName || child.tagName.toLowerCase() === 'parsererror') {
      continue;
    }
    const tag = child.tagName.toLowerCase();
    const expander = CHILD_EXPANDERS[tag];
    if (expander) {
      const result = expander(child, g);
      if (Array.isArray(result)) expanded.push(...result);
      else expanded.push(result);
      continue;
    }
    if (state.hasRecipe(child.tagName)) {
      expanded.push(child);
      continue;
    }
    throw new Error(
      `[CityGrid] unknown child <${child.tagName}>. ` +
        'Use Street, StreetRing, StreetCross, Building, BuildingRow, Block, ' +
        'Wall, WallRect, Plaza, Prop, Gate, Slot, or a registered recipe.'
    );
  }

  if (expanded.length > 0) {
    processRecipeChildElements(state, entity, 'CityGrid', expanded, context);
  }
};
