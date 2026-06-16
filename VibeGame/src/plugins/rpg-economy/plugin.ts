import { readFileSync } from 'node:fs';
import type { Plugin, Recipe } from '../../core';
import { getDataRegistry } from '../rpg-core';
import { EconomyEventBridgeSystem } from './systems';

const priceTableRecipe: Recipe = {
  name: 'PriceTable',
  parserAttributes: ['src'],
};

export const EconomyPlugin: Plugin = {
  systems: [EconomyEventBridgeSystem],
  recipes: [priceTableRecipe],
  config: {
    parsers: {
      PriceTable: ({ element, state }) => {
        const src = element.attributes['src'];
        if (typeof src !== 'string' || src.length === 0) return;
        let text: string;
        try {
          text = readFileSync(src, 'utf8');
        } catch (err) {
          console.warn(`[PriceTable] failed to load "${src}":`, err);
          return;
        }
        getDataRegistry(state).loadYaml(text);
      },
    },
  },
};
