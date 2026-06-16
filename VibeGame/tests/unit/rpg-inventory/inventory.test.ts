import { beforeEach, describe, expect, it } from 'bun:test';
import {
  INVENTORY_ADDED,
  INVENTORY_REMOVED,
  InventoryComponent,
  InventoryPlugin,
  RpgCorePlugin,
  State,
  addItem,
  getDataRegistry,
  getInventory,
  getItemQty,
  onEvent,
  removeItem,
} from 'vibegame';
import type { ItemDef } from 'vibegame';

const POTION: ItemDef = {
  id: 'potion',
  name: 'Potion',
  maxStack: 5,
  tags: [],
};
const SWORD: ItemDef = {
  id: 'sword',
  name: 'Sword',
  maxStack: 1,
  tags: [],
};
const GEM: ItemDef = {
  id: 'gem',
  name: 'Gem',
  maxStack: 99,
  tags: [],
};

function newState() {
  const state = new State();
  state.registerPlugin(RpgCorePlugin);
  state.registerPlugin(InventoryPlugin);
  for (const def of [POTION, SWORD, GEM]) {
    getDataRegistry(state).register('item', def.id, def);
  }
  return state;
}

function makeInventory(state: State, capacity: number): number {
  return state.createFromRecipe('Inventory', { capacity });
}

describe('Inventory plugin — recipe + component', () => {
  it('creates an entity with an inventory component from the recipe', () => {
    const state = newState();
    const eid = makeInventory(state, 20);
    expect(state.hasComponent(eid, InventoryComponent)).toBe(true);
    expect(InventoryComponent.capacity[eid]).toBe(20);
    expect(InventoryComponent.slots[eid]).toBe(0);
    expect(getInventory(state, eid).length).toBe(0);
  });

  it('defaults capacity when the attribute is omitted', () => {
    const state = newState();
    const eid = state.createFromRecipe('Inventory', {});
    expect(InventoryComponent.capacity[eid]).toBe(20);
  });
});

describe('Inventory plugin — add/remove items respeitando maxStack', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = newState();
    eid = makeInventory(state, 20);
  });

  it('tops up an existing stack and returns the qty that does not fit', () => {
    expect(addItem(state, eid, 'potion', 3)).toBe(0);
    expect(getItemQty(state, eid, 'potion')).toBe(3);
    expect(getInventory(state, eid).length).toBe(1);

    expect(addItem(state, eid, 'potion', 5)).toBe(3);
    expect(getItemQty(state, eid, 'potion')).toBe(5);
    expect(getInventory(state, eid).length).toBe(1);

    expect(removeItem(state, eid, 'potion', 1)).toBe(true);
    expect(getItemQty(state, eid, 'potion')).toBe(4);
  });

  it('caps a fresh stack at maxStack and returns the remainder', () => {
    expect(addItem(state, eid, 'potion', 7)).toBe(2);
    const inv = getInventory(state, eid);
    expect(inv.length).toBe(1);
    expect(inv[0].qty).toBe(5);
  });

  it('rejects a same-item add once its single stack is full', () => {
    expect(addItem(state, eid, 'sword', 1)).toBe(0);
    expect(getInventory(state, eid).length).toBe(1);
    expect(addItem(state, eid, 'sword', 1)).toBe(1);
    expect(getInventory(state, eid).length).toBe(1);
    expect(getItemQty(state, eid, 'sword')).toBe(1);
  });

  it('keeps distinct items in distinct slots', () => {
    addItem(state, eid, 'gem', 10);
    addItem(state, eid, 'potion', 2);
    const inv = getInventory(state, eid);
    expect(inv.length).toBe(2);
    expect(getItemQty(state, eid, 'gem')).toBe(10);
    expect(getItemQty(state, eid, 'potion')).toBe(2);
  });

  it('bumps the version counter on every mutation', () => {
    const before = InventoryComponent.version[eid];
    addItem(state, eid, 'gem', 1);
    const mid = InventoryComponent.version[eid];
    expect(mid).not.toBe(before);
    removeItem(state, eid, 'gem', 1);
    expect(InventoryComponent.version[eid]).not.toBe(mid);
  });

  it('removeItem compacts emptied stacks out of the slot list', () => {
    addItem(state, eid, 'potion', 5);
    expect(removeItem(state, eid, 'potion', 5)).toBe(true);
    expect(getInventory(state, eid).length).toBe(0);
    expect(InventoryComponent.slots[eid]).toBe(0);
    expect(getItemQty(state, eid, 'potion')).toBe(0);
  });

  it('removeItem is atomic: rejects without mutating when qty exceeds available', () => {
    addItem(state, eid, 'potion', 3);
    const versionBefore = InventoryComponent.version[eid];
    expect(removeItem(state, eid, 'potion', 99)).toBe(false);
    expect(getItemQty(state, eid, 'potion')).toBe(3);
    expect(InventoryComponent.version[eid]).toBe(versionBefore);
  });
});

describe('Inventory plugin — capacity overflow retorna qty rejeitado', () => {
  it('rejects a distinct item once every slot is occupied', () => {
    const state = newState();
    const eid = makeInventory(state, 2);

    const unstackables: ItemDef[] = [
      { id: 'sword', name: 'Sword', maxStack: 1, tags: [] },
      { id: 'axe', name: 'Axe', maxStack: 1, tags: [] },
      { id: 'mace', name: 'Mace', maxStack: 1, tags: [] },
    ];
    for (const def of unstackables) {
      getDataRegistry(state).register('item', def.id, def);
    }

    expect(addItem(state, eid, 'sword', 1)).toBe(0);
    expect(addItem(state, eid, 'axe', 1)).toBe(0);
    expect(addItem(state, eid, 'mace', 1)).toBe(1);

    expect(getInventory(state, eid).length).toBe(2);
    expect(getItemQty(state, eid, 'mace')).toBe(0);
  });

  it('does not emit or bump version when an add fully overflows', () => {
    const state = newState();
    const eid = makeInventory(state, 1);

    addItem(state, eid, 'sword', 1);
    state.step();

    const added: unknown[] = [];
    onEvent(state, INVENTORY_ADDED, (p) => added.push(p));
    const versionBefore = InventoryComponent.version[eid];

    expect(addItem(state, eid, 'sword', 1)).toBe(1);
    state.step();

    expect(added.length).toBe(0);
    expect(InventoryComponent.version[eid]).toBe(versionBefore);
    expect(getInventory(state, eid).length).toBe(1);
  });
});

describe('Inventory plugin — events', () => {
  it('emits INVENTORY_ADDED then INVENTORY_REMOVED via the bridge system', () => {
    const state = newState();
    const eid = makeInventory(state, 20);

    const added: unknown[] = [];
    const removed: unknown[] = [];
    onEvent(state, INVENTORY_ADDED, (p) => added.push(p));
    onEvent(state, INVENTORY_REMOVED, (p) => removed.push(p));

    addItem(state, eid, 'potion', 3);
    // events are drained by InventoryEventBridgeSystem on step().
    state.step();

    expect(added.length).toBe(1);
    expect(added[0]).toEqual({ entity: eid, itemId: 'potion', qty: 3 });

    removeItem(state, eid, 'potion', 1);
    state.step();

    expect(removed.length).toBe(1);
    expect(removed[0]).toEqual({ entity: eid, itemId: 'potion', qty: 1 });
  });

  it('reports only the qty actually placed in the added event', () => {
    const state = newState();
    const eid = makeInventory(state, 20);

    const added: unknown[] = [];
    onEvent(state, INVENTORY_ADDED, (p) => added.push(p));

    addItem(state, eid, 'potion', 9);
    state.step();

    expect(added.length).toBe(1);
    expect((added[0] as { qty: number }).qty).toBe(5);
  });
});

describe('Inventory plugin — data-driven lookup', () => {
  it('treats unregistered items as unstackable (maxStack=1)', () => {
    const state = newState();
    const eid = makeInventory(state, 5);
    expect(addItem(state, eid, 'unknown-thing', 1)).toBe(0);
    const inv = getInventory(state, eid);
    expect(inv.length).toBe(1);
    expect(inv[0].qty).toBe(1);
    expect(addItem(state, eid, 'unknown-thing', 1)).toBe(1);
  });

  it('returns 0 qty and rejects adds for entities without the component', () => {
    const state = newState();
    const bare = state.createEntity();
    expect(getItemQty(state, bare, 'potion')).toBe(0);
    expect(getInventory(state, bare).length).toBe(0);
    expect(addItem(state, bare, 'potion', 1)).toBe(1);
  });

  it('prunes the side-table when an inventory entity is destroyed', () => {
    const state = newState();
    const eid = makeInventory(state, 5);
    addItem(state, eid, 'gem', 2);
    expect(getInventory(state, eid).length).toBe(1);

    state.destroyEntity(eid);
    state.step();

    expect(state.exists(eid)).toBe(false);
    expect(getInventory(state, eid).length).toBe(0);
  });
});
