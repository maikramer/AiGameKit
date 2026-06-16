import { readFileSync, statSync } from 'node:fs';
import type { Plugin, Recipe, State } from '../../core';
import { EventBusCleanupSystem, getEventBus } from './events';
import { getDataRegistry } from './registry';

export const RpgCoreEventsPlugin: Plugin = {
  systems: [EventBusCleanupSystem],
  initialize(state) {
    getEventBus(state);
  },
};

// `<RpgData>` is a declarative loader: at scene-parse time it reads a single
// `.yaml`/`.yml`/`.json` file (sync) and ingests it into the DataRegistry. It
// creates a marker entity (no components) purely so the recipe pipeline invokes
// its parser. The recipe path is intentionally sync because the engine's
// `Parser` contract is synchronous and `Bun.file` has no sync API —
// `readFileSync` is the only option that keeps data available immediately
// after `parseXMLToEntities`. `loadDirectory` (async, Bun.file) remains the
// programmatic API.
const rpgDataRecipe: Recipe = {
  name: 'RpgData',
  components: [],
  parserAttributes: ['src'],
};

// `<LootTable>` is a domain-specific convenience over `<RpgData>`: it loads a
// `.yaml`/`.yml`/`.json` file whose top-level key is `loot-table` and ingests
// every table it contains into the registry. `id` documents the table the
// author intends to use (no runtime effect — a file may declare many tables).
const lootTableRecipe: Recipe = {
  name: 'LootTable',
  components: [],
  parserAttributes: ['src', 'id'],
};

function loadRpgDataFile(state: State, src: string): void {
  let isDir = false;
  try {
    isDir = statSync(src).isDirectory();
  } catch (err) {
    console.error(`[RpgData] cannot stat "${src}": ${(err as Error).message ?? err}`);
    return;
  }
  if (isDir) {
    console.error(
      `[RpgData] src "${src}" is a directory; use DataRegistry.loadDirectory() programmatically`
    );
    return;
  }
  let text: string;
  try {
    text = readFileSync(src, 'utf8');
  } catch (err) {
    console.error(`[RpgData] cannot read "${src}": ${(err as Error).message ?? err}`);
    return;
  }
  const registry = getDataRegistry(state);
  try {
    if (src.endsWith('.json')) registry.loadJson(text);
    else registry.loadYaml(text);
  } catch (err) {
    console.error(`[RpgData] failed to load "${src}": ${(err as Error).message ?? err}`);
  }
}

export const RpgCorePlugin: Plugin = {
  recipes: [rpgDataRecipe, lootTableRecipe],
  config: {
    parsers: {
      RpgData: ({ element, state }) => {
        const raw = element.attributes.src;
        if (raw === undefined || raw === null) return;
        const src = String(raw).trim();
        if (src.length > 0) loadRpgDataFile(state, src);
      },
      LootTable: ({ element, state }) => {
        const raw = element.attributes.src;
        if (raw === undefined || raw === null) return;
        const src = String(raw).trim();
        if (src.length > 0) loadRpgDataFile(state, src);
      },
    },
  },
  initialize(state) {
    getDataRegistry(state);
    getEventBus(state);
  },
};
