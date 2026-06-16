import type { Plugin, Recipe } from '../../core';
import { getDataRegistry } from '../rpg-core';
import { EconomyEventBridgeSystem } from './systems';

type NodeFsLike = { readFileSync(path: string, encoding: string): string };

// Lazy, browser-safe fs access (mirrors `acquireNodeFs` in rpg-core/plugin.ts).
// A top-level `import { readFileSync } from 'node:fs'` gets pulled into the
// browser bundle via the `vibegame` barrel and crashes the app on load
// ("node:fs has been externalized for browser compatibility").
function acquireNodeFs(): NodeFsLike | null {
  try {
    const req = (import.meta as { require?: (id: string) => NodeFsLike })
      .require;
    if (typeof req !== 'function') return null;
    return req('node:fs');
  } catch {
    return null;
  }
}

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
        const fs = acquireNodeFs();
        if (!fs) {
          console.error(
            `[PriceTable] filesystem loading is unavailable in this environment (browser). Cannot load "${src}".`
          );
          return;
        }
        let text: string;
        try {
          text = fs.readFileSync(src, 'utf8');
        } catch (err) {
          console.warn(`[PriceTable] failed to load "${src}":`, err);
          return;
        }
        getDataRegistry(state).loadYaml(text);
      },
    },
  },
};
