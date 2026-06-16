import type { Plugin, Recipe, State } from '../../core';
import { EventBusCleanupSystem, getEventBus } from './events';
import { getDataRegistry } from './registry';

type NodeFsLike = {
  readFileSync(path: string, encoding: string): string;
  statSync(path: string): { isDirectory(): boolean };
};

function acquireNodeFs(): NodeFsLike | null {
  try {
    // `import.meta.require` is available in Bun (and Node with a loader);
    // undefined in the browser, so bundlers never resolve `node:fs` for
    // browser builds. This replaces `(new Function('return require'))()`,
    // which fails in Bun ESM where bare `require` is not defined.
    const req = (
      import.meta as { require?: (id: string) => NodeFsLike }
    ).require;
    if (typeof req !== 'function') return null;
    return req('node:fs');
  } catch {
    return null;
  }
}

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
  const fs = acquireNodeFs();
  if (!fs) {
    console.error(
      `[RpgData] filesystem data loading is unavailable in this environment (browser). Cannot load "${src}".`
    );
    return;
  }
  let isDir = false;
  try {
    isDir = fs.statSync(src).isDirectory();
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
    text = fs.readFileSync(src, 'utf8');
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
