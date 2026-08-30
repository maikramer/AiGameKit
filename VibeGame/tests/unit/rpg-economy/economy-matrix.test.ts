import { beforeEach, describe, expect, it } from 'bun:test';
import {
  EconomyPlugin,
  GOLD_KIND,
  InventoryPlugin,
  RpgCorePlugin,
  RpgVaultPlugin,
  State,
  addItem,
  addResource,
  buyItem,
  getDataRegistry,
  getItemQty,
  getPrice,
  getResource,
  sellItem,
} from 'aigamekit-vibegame';
import type { ItemDef, PriceEntry } from 'aigamekit-vibegame';

function newState(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(InventoryPlugin);
  state.registerPlugin(EconomyPlugin);
  return state;
}

function registerItem(state: State, id: string, maxStack: number): void {
  getDataRegistry(state).register('item', id, {
    id,
    name: id,
    maxStack,
    tags: [],
  } satisfies ItemDef);
}

function registerPrice(state: State, id: string, entry: PriceEntry): void {
  getDataRegistry(state).register('price', id, entry);
}

describe('Economy matrix — GOLD_KIND', () => {
  it('GOLD_KIND is the string gold', () => {
    expect(GOLD_KIND).toBe('gold');
  });
});

describe('Economy matrix — getPrice registry', () => {
  const catalog: Array<{ id: string; buy: number; sell: number }> = [];
  for (let i = 0; i < 50; i++) {
    catalog.push({ id: `trade-item-${i}`, buy: 10 + i, sell: 5 + i });
  }

  for (const row of catalog) {
    it(`getPrice buy ${row.id} → ${row.buy}`, () => {
      const state = newState();
      registerPrice(state, row.id, { buy: row.buy, sell: row.sell });
      expect(getPrice(state, row.id, 'buy')).toBe(row.buy);
    });

    it(`getPrice sell ${row.id} → ${row.sell}`, () => {
      const state = newState();
      registerPrice(state, row.id, { buy: row.buy, sell: row.sell });
      expect(getPrice(state, row.id, 'sell')).toBe(row.sell);
    });
  }
});

describe('Economy matrix — buyItem qty grid', () => {
  let state: State;
  let buyer: number;
  let seller: number;

  beforeEach(() => {
    state = newState();
    registerItem(state, 'widget', 99);
    registerPrice(state, 'widget', { buy: 10, sell: 4 });
    buyer = state.createFromRecipe('Inventory', { capacity: 20 });
    seller = state.createFromRecipe('Inventory', { capacity: 20 });
    addResource(state, buyer, GOLD_KIND, 1000);
    addItem(state, seller, 'widget', 50);
  });

  for (let qty = 1; qty <= 10; qty++) {
    it(`buyItem succeeds for qty=${qty} at price 10`, () => {
      const goldBefore = getResource(state, buyer, GOLD_KIND);
      const ok = buyItem(state, buyer, seller, 'widget', qty, 10);
      expect(ok).toBe(true);
      expect(getItemQty(state, buyer, 'widget')).toBe(qty);
      expect(getResource(state, buyer, GOLD_KIND)).toBe(goldBefore - qty * 10);
    });
  }
});

describe('Economy matrix — buyItem rejects insufficient gold', () => {
  for (let gold = 0; gold < 10; gold++) {
    it(`buyItem fails when buyer has gold=${gold}`, () => {
      const state = newState();
      registerItem(state, 'gem', 10);
      const buyer = state.createFromRecipe('Inventory', { capacity: 5 });
      const seller = state.createFromRecipe('Inventory', { capacity: 5 });
      addResource(state, buyer, GOLD_KIND, gold);
      addItem(state, seller, 'gem', 5);
      expect(buyItem(state, buyer, seller, 'gem', 1, 10)).toBe(false);
      expect(getItemQty(state, buyer, 'gem')).toBe(0);
      expect(getResource(state, buyer, GOLD_KIND)).toBe(gold);
    });
  }
});

describe('Economy matrix — sellItem qty grid', () => {
  for (let qty = 1; qty <= 10; qty++) {
    it(`sellItem moves qty=${qty} at sell price 7`, () => {
      const state = newState();
      registerItem(state, 'ore', 99);
      const player = state.createFromRecipe('Inventory', { capacity: 20 });
      const merchant = state.createFromRecipe('Inventory', { capacity: 20 });
      addResource(state, merchant, GOLD_KIND, 500);
      addItem(state, player, 'ore', 20);
      expect(sellItem(state, player, merchant, 'ore', qty, 7)).toBe(true);
      expect(getItemQty(state, player, 'ore')).toBe(20 - qty);
      expect(getResource(state, player, GOLD_KIND)).toBe(qty * 7);
    });
  }
});

describe('Economy matrix — getPrice missing / invalid', () => {
  const missingIds = Array.from({ length: 20 }, (_, i) => `missing-${i}`);

  for (const id of missingIds) {
    it(`getPrice returns 0 buy for unregistered ${id}`, () => {
      const state = newState();
      expect(getPrice(state, id, 'buy')).toBe(0);
    });
  }
});
