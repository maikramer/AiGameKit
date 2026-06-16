import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  InventoryPlugin,
  RpgCorePlugin,
  RpgVaultPlugin,
  State,
  XMLParser,
  getDataRegistry,
  getItemQty,
  getResource,
  parseXMLToEntities,
  rollLoot,
  applyLootResult,
  onEvent,
  LOOT_ROLLED,
  LOOT_DROPPED,
} from 'vibegame';
import type { LootResult, LootTable, RngFn } from 'vibegame';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

/** Deterministic sequential RNG: returns scripted values, cycling. */
function seqRng(values: number[]): RngFn {
  let i = 0;
  return () => values[i++ % values.length];
}

/** Deterministic mulberry32 PRNG for distribution tests. */
function mulberry32(seed: number): RngFn {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function newStateWithPlugins(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(InventoryPlugin);
  return state;
}

function registerTable(
  state: State,
  id: string,
  table: Omit<LootTable, 'id'>
): void {
  getDataRegistry(state).register<LootTable>('loot-table', id, {
    id,
    ...table,
  });
}

const POTION = {
  id: 'potion',
  name: 'Potion',
  maxStack: 99,
  tags: ['consumable'],
};
const GOLD_COIN = {
  id: 'gold-coin',
  name: 'Gold Coin',
  maxStack: 9999,
  tags: ['currency'],
};

describe('rollLoot — deterministic rolling', () => {
  it('returns a deterministic result for a scripted RNG', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'chest', {
      rolls: 1,
      entries: [
        { itemId: 'gold-coin', qtyMin: 1, qtyMax: 10, weight: 1 },
        { itemId: 'potion', qtyMin: 1, qtyMax: 1, weight: 1 },
      ],
    });

    // selection draw: 0.2 * totalWeight(2) = 0.4 -> first entry (gold-coin)
    // qty draw: 0.5 -> 1 + floor(0.5 * 10) = 6
    const results = rollLoot(state, 'chest', undefined, seqRng([0.2, 0.5]));
    expect(results).toEqual([{ itemId: 'gold-coin', qty: 6 }] as LootResult[]);
  });

  it('uses table.rolls when rolls is omitted', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'multi', {
      rolls: 3,
      entries: [{ itemId: 'arrow', qtyMin: 1, qtyMax: 1, weight: 1 }],
    });

    const results = rollLoot(state, 'multi', undefined, seqRng([0.0]));
    expect(results.length).toBe(3);
    for (const r of results) expect(r.itemId).toBe('arrow');
  });

  it('honours an explicit rolls argument that overrides table.rolls', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'one', {
      rolls: 1,
      entries: [{ itemId: 'arrow', qtyMin: 1, qtyMax: 1, weight: 1 }],
    });

    const results = rollLoot(state, 'one', 5, seqRng([0.0]));
    expect(results.length).toBe(5);
  });

  it('returns [] for an unknown table id', () => {
    const state = newStateWithPlugins();
    expect(rollLoot(state, 'does-not-exist')).toEqual([]);
  });

  it('returns [] for a table with no entries', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'empty', { rolls: 1, entries: [] });
    expect(rollLoot(state, 'empty')).toEqual([]);
  });

  it('clamps qty to the [qtyMin, qtyMax] inclusive range', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'fixed', {
      rolls: 1,
      entries: [{ itemId: 'gem', qtyMin: 5, qtyMax: 5, weight: 1 }],
    });
    const results = rollLoot(state, 'fixed', undefined, seqRng([0.99]));
    expect(results[0].qty).toBe(5);
  });

  it('produces independent rolls (no carry-over between rolls)', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'two', {
      rolls: 2,
      entries: [
        { itemId: 'a', qtyMin: 1, qtyMax: 1, weight: 1 },
        { itemId: 'b', qtyMin: 1, qtyMax: 1, weight: 1 },
      ],
    });
    // selection draws: 0.1 -> a, 0.9 -> b  (qty always 1)
    const results = rollLoot(
      state,
      'two',
      undefined,
      seqRng([0.1, 0.99, 0.9, 0.99])
    );
    expect(results.map((r) => r.itemId)).toEqual(['a', 'b']);
  });

  it('emits a LOOT_ROLLED event with the tableId and results', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'ev', {
      rolls: 1,
      entries: [{ itemId: 'potion', qtyMin: 1, qtyMax: 1, weight: 1 }],
    });
    const seen: unknown[] = [];
    onEvent(state, LOOT_ROLLED, (p) => seen.push(p));

    rollLoot(state, 'ev', undefined, seqRng([0.0]));
    expect(seen.length).toBe(1);
    const payload = seen[0] as { tableId: string; results: LootResult[] };
    expect(payload.tableId).toBe('ev');
    expect(payload.results.length).toBe(1);
  });
});

describe('rollLoot — weighted distribution', () => {
  it('skips entries with zero or negative weight', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'zw', {
      rolls: 1,
      entries: [
        { itemId: 'rare', qtyMin: 1, qtyMax: 1, weight: 0 },
        { itemId: 'common', qtyMin: 1, qtyMax: 1, weight: -5 },
        { itemId: 'normal', qtyMin: 1, qtyMax: 1, weight: 1 },
      ],
    });
    const rng = mulberry32(42);
    for (let i = 0; i < 50; i++) {
      const r = rollLoot(state, 'zw', undefined, rng);
      expect(r[0].itemId).toBe('normal');
    }
  });

  it('approximates the configured weight ratio over many rolls', () => {
    const state = newStateWithPlugins();
    registerTable(state, 'dist', {
      rolls: 1,
      entries: [
        { itemId: 'A', qtyMin: 1, qtyMax: 1, weight: 1 },
        { itemId: 'B', qtyMin: 1, qtyMax: 1, weight: 9 },
      ],
    });
    const rng = mulberry32(7);
    let a = 0;
    let b = 0;
    const N = 1000;
    for (let i = 0; i < N; i++) {
      const r = rollLoot(state, 'dist', undefined, rng);
      if (r[0].itemId === 'A') a++;
      else b++;
    }
    // B should dominate and land near 90%.
    expect(b).toBeGreaterThan(a);
    const bRatio = b / N;
    expect(bRatio).toBeGreaterThan(0.83);
    expect(bRatio).toBeLessThan(0.97);
  });
});

describe('applyLootResult — routing', () => {
  it('routes an itemId result into the inventory', () => {
    const state = newStateWithPlugins();
    getDataRegistry(state).register('item', 'potion', POTION);
    const eid = state.createFromRecipe('Inventory', { capacity: 8 });

    applyLootResult(state, eid, { itemId: 'potion', qty: 1 });

    expect(getItemQty(state, eid, 'potion')).toBe(1);
  });

  it('routes a resourceKind result into the vault', () => {
    const state = newStateWithPlugins();
    const eid = state.createEntity();

    applyLootResult(state, eid, { resourceKind: 'gold', qty: 50 });

    expect(getResource(state, eid, 'gold')).toBe(50);
  });

  it('is a no-op when qty <= 0', () => {
    const state = newStateWithPlugins();
    getDataRegistry(state).register('item', 'potion', POTION);
    const eid = state.createFromRecipe('Inventory', { capacity: 8 });

    applyLootResult(state, eid, { itemId: 'potion', qty: 0 });
    expect(getItemQty(state, eid, 'potion')).toBe(0);
  });

  it('emits a LOOT_DROPPED event on routing', () => {
    const state = newStateWithPlugins();
    getDataRegistry(state).register('item', 'gold-coin', GOLD_COIN);
    const eid = state.createFromRecipe('Inventory', { capacity: 8 });
    const seen: unknown[] = [];
    onEvent(state, LOOT_DROPPED, (p) => seen.push(p));

    applyLootResult(state, eid, { itemId: 'gold-coin', qty: 3 });
    expect(seen.length).toBe(1);
    const payload = seen[0] as { entity: number; itemId: string; qty: number };
    expect(payload.entity).toBe(eid);
    expect(payload.itemId).toBe('gold-coin');
    expect(payload.qty).toBe(3);
  });

  it('end-to-end: roll then apply lands the loot on the receiver', () => {
    const state = newStateWithPlugins();
    getDataRegistry(state).register('item', 'potion', POTION);
    getDataRegistry(state).register('item', 'gold-coin', GOLD_COIN);
    registerTable(state, 'chest-common', {
      rolls: 1,
      entries: [
        { itemId: 'potion', qtyMin: 1, qtyMax: 1, weight: 1 },
        { resourceKind: 'gold', qtyMin: 10, qtyMax: 50, weight: 2 },
      ],
    });
    const player = state.createFromRecipe('Inventory', { capacity: 8 });

    const results = rollLoot(
      state,
      'chest-common',
      undefined,
      seqRng([0.5, 0.5])
    );
    for (const r of results) applyLootResult(state, player, r);

    // r = 0.5*3 = 1.5; potion(w1) -> r=0.5; gold(w2) -> r=-1.5 -> gold.
    // qty = 10 + floor(0.5 * (50-10+1)) = 10 + 20 = 30.
    expect(results.length).toBe(1);
    expect(results[0].resourceKind).toBe('gold');
    expect(results[0].qty).toBe(30);
    expect(getResource(state, player, 'gold')).toBe(30);
  });
});

describe('<LootTable> recipe loading', () => {
  it('loads a loot-table YAML file into the registry via XML', () => {
    const dir = mkdtempSync(join(tmpdir(), 'loot-'));
    const file = join(dir, 'chest.yaml');
    writeFileSync(
      file,
      [
        'loot-table:',
        '  chest-xml:',
        '    rolls: 1',
        '    entries:',
        '      - itemId: potion',
        '        qtyMin: 1',
        '        qtyMax: 1',
        '        weight: 1',
        '',
      ].join('\n'),
      'utf8'
    );

    const state = newStateWithPlugins();
    const xml = `<Scene><LootTable id="chest-xml" src="${file}"/></Scene>`;
    parseXMLToEntities(state, XMLParser.parse(xml).root);

    const table = getDataRegistry(state).get<LootTable>(
      'loot-table',
      'chest-xml'
    );
    expect(table).toBeDefined();
    expect(table!.rolls).toBe(1);
    expect(table!.entries.length).toBe(1);
    expect(table!.entries[0].itemId).toBe('potion');

    const results = rollLoot(state, 'chest-xml', undefined, seqRng([0.0]));
    expect(results).toEqual([{ itemId: 'potion', qty: 1 }]);
  });
});
