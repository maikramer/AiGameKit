import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  ECONOMY_GAINED,
  ECONOMY_SPENT,
  EconomyPlugin,
  INVENTORY_ADDED,
  INVENTORY_REMOVED,
  InventoryPlugin,
  RpgCorePlugin,
  RpgVaultPlugin,
  State,
  XMLParser,
  addResource,
  addItem,
  buyItem,
  getDataRegistry,
  getInventory,
  getResource,
  getPrice,
  getItemQty,
  onEvent,
  parseXMLToEntities,
  sellItem,
} from 'vibegame';
import type { ItemDef, PriceEntry } from 'vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

const POTION: ItemDef = {
  id: 'potion',
  name: 'Potion',
  maxStack: 20,
  tags: [],
};
const GEM: ItemDef = {
  id: 'gem',
  name: 'Gem',
  maxStack: 99,
  tags: [],
};
const SWORD: ItemDef = {
  id: 'sword',
  name: 'Sword',
  maxStack: 1,
  tags: [],
};

const POTION_PRICE: PriceEntry = { buy: 15, sell: 7 };

function newState(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(RpgVaultPlugin);
  state.registerPlugin(InventoryPlugin);
  state.registerPlugin(EconomyPlugin);
  for (const def of [POTION, GEM, SWORD]) {
    getDataRegistry(state).register('item', def.id, def);
  }
  getDataRegistry(state).register('price', 'potion', POTION_PRICE);
  return state;
}

function makeActor(state: State, capacity = 20): number {
  return state.createFromRecipe('Inventory', { capacity });
}

function fund(state: State, eid: number, gold: number): void {
  addResource(state, eid, 'gold', gold);
}

function stock(state: State, eid: number, itemId: string, qty: number): void {
  addItem(state, eid, itemId, qty);
}

describe('Economy plugin — getPrice (data-driven)', () => {
  it('reads buy/sell prices from the price registry kind', () => {
    const state = newState();
    expect(getPrice(state, 'potion', 'buy')).toBe(15);
    expect(getPrice(state, 'potion', 'sell')).toBe(7);
  });

  it('returns 0 for unpriced items or missing fields', () => {
    const state = newState();
    expect(getPrice(state, 'sword', 'buy')).toBe(0);
    expect(getPrice(state, 'unknown', 'buy')).toBe(0);
  });
});

describe('Economy plugin — buy atomic transaction', () => {
  it('moves gold and items consistently on a successful buy', () => {
    const state = newState();
    const buyer = makeActor(state, 5);
    const seller = makeActor(state, 20);
    fund(state, buyer, 100);
    stock(state, seller, 'potion', 10);

    expect(buyItem(state, buyer, seller, 'potion', 3, 15)).toBe(true);

    expect(getResource(state, buyer, 'gold')).toBe(55);
    expect(getResource(state, seller, 'gold')).toBe(45);
    expect(getItemQty(state, buyer, 'potion')).toBe(3);
    expect(getItemQty(state, seller, 'potion')).toBe(7);
  });

  it('keeps both sides unchanged when the buyer cannot afford the price', () => {
    const state = newState();
    const buyer = makeActor(state, 5);
    const seller = makeActor(state, 20);
    fund(state, buyer, 10);
    stock(state, seller, 'potion', 10);

    expect(buyItem(state, buyer, seller, 'potion', 1, 15)).toBe(false);

    expect(getResource(state, buyer, 'gold')).toBe(10);
    expect(getResource(state, seller, 'gold')).toBe(0);
    expect(getItemQty(state, buyer, 'potion')).toBe(0);
    expect(getItemQty(state, seller, 'potion')).toBe(10);
  });

  it('rolls back when the buyer inventory cannot accept the qty', () => {
    const state = newState();
    const buyer = makeActor(state, 1);
    const seller = makeActor(state, 20);
    fund(state, buyer, 500);
    stock(state, seller, 'gem', 10);
    stock(state, buyer, 'sword', 1);

    expect(buyItem(state, buyer, seller, 'gem', 1, 10)).toBe(false);

    expect(getResource(state, buyer, 'gold')).toBe(500);
    expect(getResource(state, seller, 'gold')).toBe(0);
    expect(getItemQty(state, buyer, 'gem')).toBe(0);
    expect(getItemQty(state, seller, 'gem')).toBe(10);
    expect(getInventory(state, buyer).length).toBe(1);
  });

  it('rolls back when the buyer stack would overflow maxStack', () => {
    const state = newState();
    const buyer = makeActor(state, 5);
    const seller = makeActor(state, 20);
    fund(state, buyer, 500);
    stock(state, seller, 'potion', 20);
    stock(state, buyer, 'potion', 18);

    expect(buyItem(state, buyer, seller, 'potion', 5, 10)).toBe(false);

    expect(getResource(state, buyer, 'gold')).toBe(500);
    expect(getItemQty(state, buyer, 'potion')).toBe(18);
    expect(getItemQty(state, seller, 'potion')).toBe(20);
  });

  it('rolls back when the seller does not have enough stock', () => {
    const state = newState();
    const buyer = makeActor(state, 5);
    const seller = makeActor(state, 20);
    fund(state, buyer, 500);
    stock(state, seller, 'potion', 2);

    expect(buyItem(state, buyer, seller, 'potion', 3, 15)).toBe(false);

    expect(getResource(state, buyer, 'gold')).toBe(500);
    expect(getResource(state, seller, 'gold')).toBe(0);
    expect(getItemQty(state, buyer, 'potion')).toBe(0);
    expect(getItemQty(state, seller, 'potion')).toBe(2);
  });

  it('rejects non-positive qty without mutating', () => {
    const state = newState();
    const buyer = makeActor(state, 5);
    const seller = makeActor(state, 20);
    fund(state, buyer, 100);
    stock(state, seller, 'potion', 5);

    expect(buyItem(state, buyer, seller, 'potion', 0, 15)).toBe(false);
    expect(buyItem(state, buyer, seller, 'potion', -3, 15)).toBe(false);
    expect(getResource(state, buyer, 'gold')).toBe(100);
    expect(getItemQty(state, seller, 'potion')).toBe(5);
  });
});

describe('Economy plugin — sell is symmetric to buy', () => {
  it('moves gold and items consistently on a successful sell', () => {
    const state = newState();
    const player = makeActor(state, 20);
    const merchant = makeActor(state, 20);
    fund(state, merchant, 100);
    stock(state, player, 'potion', 10);

    expect(sellItem(state, player, merchant, 'potion', 3, 7)).toBe(true);

    expect(getResource(state, player, 'gold')).toBe(21);
    expect(getResource(state, merchant, 'gold')).toBe(79);
    expect(getItemQty(state, player, 'potion')).toBe(7);
    expect(getItemQty(state, merchant, 'potion')).toBe(3);
  });

  it('rejects the sell when the buyer (merchant) has no gold', () => {
    const state = newState();
    const player = makeActor(state, 20);
    const merchant = makeActor(state, 20);
    stock(state, player, 'potion', 10);

    expect(sellItem(state, player, merchant, 'potion', 1, 7)).toBe(false);

    expect(getResource(state, player, 'gold')).toBe(0);
    expect(getItemQty(state, player, 'potion')).toBe(10);
    expect(getItemQty(state, merchant, 'potion')).toBe(0);
  });
});

describe('Economy plugin — events flow through underlying systems', () => {
  let state: State;
  let buyer: number;
  let seller: number;
  const spent: unknown[] = [];
  const gained: unknown[] = [];
  const invAdded: unknown[] = [];
  const invRemoved: unknown[] = [];

  beforeEach(() => {
    state = newState();
    buyer = makeActor(state, 5);
    seller = makeActor(state, 20);
    fund(state, buyer, 100);
    stock(state, seller, 'potion', 10);
    state.step();
    spent.length = 0;
    gained.length = 0;
    invAdded.length = 0;
    invRemoved.length = 0;
    onEvent(state, ECONOMY_SPENT, (p) => spent.push(p));
    onEvent(state, ECONOMY_GAINED, (p) => gained.push(p));
    onEvent(state, INVENTORY_ADDED, (p) => invAdded.push(p));
    onEvent(state, INVENTORY_REMOVED, (p) => invRemoved.push(p));
  });

  it('emits ECONOMY_SPENT/GAINED synchronously and INVENTORY_* via the bridge step', () => {
    buyItem(state, buyer, seller, 'potion', 2, 15);

    expect(spent).toEqual([{ entity: buyer, kind: 'gold', amount: 30 }]);
    expect(gained).toEqual([{ entity: seller, kind: 'gold', amount: 30 }]);

    expect(invAdded.length).toBe(0);
    expect(invRemoved.length).toBe(0);

    state.step();

    expect(invAdded).toEqual([{ entity: buyer, itemId: 'potion', qty: 2 }]);
    expect(invRemoved).toEqual([{ entity: seller, itemId: 'potion', qty: 2 }]);
  });

  it('emits no events on a rejected transaction', () => {
    expect(buyItem(state, buyer, seller, 'potion', 99, 15)).toBe(false);
    state.step();
    expect(spent.length).toBe(0);
    expect(gained.length).toBe(0);
    expect(invAdded.length).toBe(0);
    expect(invRemoved.length).toBe(0);
  });
});

describe('Economy plugin — <PriceTable> recipe loads a YAML file', () => {
  it('registers prices into the data registry from src', () => {
    const dir = mkdtempSync(join(tmpdir(), 'economy-prices-'));
    const yamlPath = join(dir, 'prices.yaml');
    writeFileSync(
      yamlPath,
      [
        'price:',
        '  potion:',
        '    buy: 15',
        '    sell: 7',
        '  gem:',
        '    buy: 40',
        '    sell: 20',
        '',
      ].join('\n')
    );

    const state = new State();
    state.registerPlugin(RpgCorePlugin);
    state.registerPlugin(RpgVaultPlugin);
    state.registerPlugin(InventoryPlugin);
    state.registerPlugin(EconomyPlugin);

    const xml = `<Scene><PriceTable src="${yamlPath}"/></Scene>`;
    parseXMLToEntities(state, XMLParser.parse(xml).root);

    expect(getPrice(state, 'potion', 'buy')).toBe(15);
    expect(getPrice(state, 'potion', 'sell')).toBe(7);
    expect(getPrice(state, 'gem', 'buy')).toBe(40);
    expect(getPrice(state, 'gem', 'sell')).toBe(20);
  });
});
