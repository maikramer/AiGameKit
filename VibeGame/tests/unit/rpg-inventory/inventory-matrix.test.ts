import { describe, expect, it } from 'bun:test';
import {
  InventoryComponent,
  InventoryPlugin,
  RpgCorePlugin,
  State,
  addItem,
  getDataRegistry,
  getInventory,
  getItemQty,
  removeItem,
} from 'vibegame';
import {
  applyInventoryEntitySnapshot,
  getInventoryEntitySnapshot,
} from '../../../src/plugins/rpg-inventory/systems';
import type { ItemDef } from 'vibegame';

function newState(): State {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(InventoryPlugin);
  return state;
}

function registerStackable(state: State, id: string, maxStack: number): void {
  getDataRegistry(state).register('item', id, {
    id,
    name: id,
    maxStack,
    tags: [],
  } satisfies ItemDef);
}

describe('Inventory matrix — addItem overflow remainder', () => {
  for (let maxStack = 1; maxStack <= 10; maxStack++) {
    it(`addItem caps at maxStack=${maxStack} and returns remainder`, () => {
      const state = newState();
      registerStackable(state, 'stack', maxStack);
      const eid = state.createFromRecipe('Inventory', { capacity: 10 });
      const request = maxStack + 3;
      const leftover = addItem(state, eid, 'stack', request);
      expect(leftover).toBe(3);
      expect(getItemQty(state, eid, 'stack')).toBe(maxStack);
    });
  }
});

describe('Inventory matrix — distinct slots by capacity', () => {
  for (let cap = 1; cap <= 10; cap++) {
    it(`capacity=${cap} accepts at most ${cap} distinct item types`, () => {
      const state = newState();
      const eid = state.createFromRecipe('Inventory', { capacity: cap });
      for (let i = 0; i < cap; i++) {
        const id = `slot-${i}`;
        registerStackable(state, id, 99);
        expect(addItem(state, eid, id, 1)).toBe(0);
      }
      registerStackable(state, 'overflow', 99);
      expect(addItem(state, eid, 'overflow', 1)).toBe(1);
      expect(getInventory(state, eid).length).toBe(cap);
    });
  }
});

describe('Inventory matrix — removeItem partial', () => {
  for (let take = 1; take <= 10; take++) {
    it(`removeItem takes qty=${take} from stack of 15`, () => {
      const state = newState();
      registerStackable(state, 'bulk', 99);
      const eid = state.createFromRecipe('Inventory', { capacity: 5 });
      addItem(state, eid, 'bulk', 15);
      expect(removeItem(state, eid, 'bulk', take)).toBe(true);
      expect(getItemQty(state, eid, 'bulk')).toBe(15 - take);
    });
  }
});

describe('Inventory matrix — snapshot roundtrip', () => {
  for (let i = 0; i < 20; i++) {
    it(`snapshot roundtrip preserves stacks set ${i}`, () => {
      const state = newState();
      registerStackable(state, 'a', 99);
      registerStackable(state, 'b', 99);
      const eid = state.createFromRecipe('Inventory', { capacity: 10 });
      addItem(state, eid, 'a', 2 + i);
      addItem(state, eid, 'b', 1);
      const snap = getInventoryEntitySnapshot(state, eid)!;
      applyInventoryEntitySnapshot(state, eid, { capacity: 3, stacks: [] });
      expect(getInventory(state, eid).length).toBe(0);
      applyInventoryEntitySnapshot(state, eid, snap);
      expect(getItemQty(state, eid, 'a')).toBe(2 + i);
      expect(getItemQty(state, eid, 'b')).toBe(1);
      expect(InventoryComponent.capacity[eid]).toBe(10);
    });
  }
});

describe('Inventory matrix — zero qty edge cases', () => {
  it('addItem qty 0 returns 0 without bumping slots', () => {
    const state = newState();
    const eid = state.createFromRecipe('Inventory', { capacity: 5 });
    expect(addItem(state, eid, 'x', 0)).toBe(0);
    expect(InventoryComponent.slots[eid]).toBe(0);
  });

  for (let i = 0; i < 19; i++) {
    it(`removeItem qty 0 is no-op #${i}`, () => {
      const state = newState();
      registerStackable(state, 'p', 5);
      const eid = state.createFromRecipe('Inventory', { capacity: 5 });
      addItem(state, eid, 'p', 3);
      const v = InventoryComponent.version[eid];
      expect(removeItem(state, eid, 'p', 0)).toBe(true);
      expect(getItemQty(state, eid, 'p')).toBe(3);
      expect(InventoryComponent.version[eid]).toBe(v);
    });
  }
});

describe('Inventory matrix — getInventoryEntitySnapshot null', () => {
  for (let i = 0; i < 20; i++) {
    it(`snapshot null for bare entity #${i}`, () => {
      const state = newState();
      const eid = state.createEntity();
      expect(getInventoryEntitySnapshot(state, eid)).toBeNull();
    });
  }
});
