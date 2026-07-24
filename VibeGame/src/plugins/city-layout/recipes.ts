import type { Recipe } from '../../core';

const CELL_PAIR = ['from', 'to', 'min', 'max', 'at'] as const;

export const cityGridRecipe: Recipe = {
  name: 'CityGrid',
  components: ['transform'],
  parserOwnsChildren: true,
  parserAttributes: ['cell', 'origin', 'align-to-terrain'],
};

function childRecipe(
  name: string,
  parserAttributes: readonly string[]
): Recipe {
  return {
    name,
    components: ['transform'],
    parserAttributes: [...parserAttributes],
  };
}

export const streetRecipe = childRecipe('Street', [
  'from',
  'to',
  'width',
  'texture-url',
]);
export const streetRingRecipe = childRecipe('StreetRing', [
  'min',
  'max',
  'from',
  'to',
  'width',
  'texture-url',
]);
export const streetCrossRecipe = childRecipe('StreetCross', [
  'min',
  'max',
  'from',
  'to',
  'width',
  'texture-url',
]);
export const buildingRecipe = childRecipe('Building', [
  'at',
  'prefab',
  'url',
  'rot',
  'name',
  'scale',
]);
export const buildingRowRecipe = childRecipe('BuildingRow', [
  'from',
  'to',
  'step',
  'prefab',
  'url',
  'rot',
  'name',
]);
export const blockRecipe = childRecipe('Block', [
  'min',
  'max',
  'from',
  'to',
  'mode',
  'step',
  'prefab',
  'url',
  'rot',
  'name',
]);
export const wallRecipe = childRecipe('Wall', [
  'from',
  'to',
  'height',
  'thickness',
  'color',
  'texture-url',
  'normal-map-url',
  'texture-repeat',
  'name',
]);
export const wallRectRecipe = childRecipe('WallRect', [
  'min',
  'max',
  'from',
  'to',
  'height',
  'thickness',
  'color',
  'texture-url',
  'normal-map-url',
  'texture-repeat',
  'gates',
  'gate-prefab',
]);
export const plazaRecipe = childRecipe('Plaza', [
  'min',
  'max',
  'from',
  'to',
  'color',
  'texture-url',
  'texture-repeat',
  'edge-feather',
  'name',
]);
export const propRecipe = childRecipe('Prop', [
  'at',
  'prefab',
  'url',
  'rot',
  'name',
  'scale',
  'collider',
]);
export const slotRecipe = childRecipe('Slot', ['at', 'role', 'name']);
export const gateRecipe = childRecipe('Gate', [
  'at',
  'facing',
  'prefab',
  'url',
  'rot',
  'name',
]);

export const CITY_LAYOUT_RECIPES: Recipe[] = [
  cityGridRecipe,
  streetRecipe,
  streetRingRecipe,
  streetCrossRecipe,
  buildingRecipe,
  buildingRowRecipe,
  blockRecipe,
  wallRecipe,
  wallRectRecipe,
  plazaRecipe,
  propRecipe,
  slotRecipe,
  gateRecipe,
];

void CELL_PAIR;
