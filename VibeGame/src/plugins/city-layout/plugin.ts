import type { Plugin } from '../../core';
import {
  blockParser,
  buildingParser,
  buildingRowParser,
  cityGridParser,
  gateParser,
  plazaParser,
  propParser,
  slotParser,
  streetCrossParser,
  streetParser,
  streetRingParser,
  wallParser,
  wallRectParser,
} from './parser';
import { CITY_LAYOUT_RECIPES } from './recipes';

export const CityLayoutPlugin: Plugin = {
  recipes: CITY_LAYOUT_RECIPES,
  config: {
    parsers: {
      CityGrid: cityGridParser,
      Street: streetParser,
      StreetRing: streetRingParser,
      StreetCross: streetCrossParser,
      Building: buildingParser,
      BuildingRow: buildingRowParser,
      Block: blockParser,
      Wall: wallParser,
      WallRect: wallRectParser,
      Plaza: plazaParser,
      Prop: propParser,
      Slot: slotParser,
      Gate: gateParser,
    },
  },
};
