import type { State, System } from '../../core';
import { InventoryComponent } from './components';
import {
  emitEvent,
  getDataRegistry,
  INVENTORY_ADDED,
  INVENTORY_REMOVED,
} from '../rpg-core';
import type { ItemDef, ItemStack } from '../rpg-core/types';

interface PendingEvent {
  readonly event: string;
  readonly payload: unknown;
}

const stacksByEntity = new WeakMap<State, Map<number, ItemStack[]>>();
const pendingEvents = new WeakMap<State, PendingEvent[]>();

const EMPTY_STACKS: readonly ItemStack[] = Object.freeze([]);

function entityTable(state: State): Map<number, ItemStack[]> {
  let table = stacksByEntity.get(state);
  if (!table) {
    table = new Map();
    stacksByEntity.set(state, table);
  }
  return table;
}

function pendingQueue(state: State): PendingEvent[] {
  let queue = pendingEvents.get(state);
  if (!queue) {
    queue = [];
    pendingEvents.set(state, queue);
  }
  return queue;
}

function getStacks(state: State, eid: number): ItemStack[] {
  const table = entityTable(state);
  let stacks = table.get(eid);
  if (!stacks) {
    stacks = [];
    table.set(eid, stacks);
  }
  return stacks;
}

function resolveMaxStack(state: State, itemId: string): number {
  const def = getDataRegistry(state).get<ItemDef>('item', itemId);
  if (!def || typeof def.maxStack !== 'number' || def.maxStack < 1) {
    return 1;
  }
  return Math.floor(def.maxStack);
}

function bump(eid: number): void {
  InventoryComponent.version[eid] = (InventoryComponent.version[eid] + 1) >>> 0;
}

export function getInventory(state: State, eid: number): readonly ItemStack[] {
  if (!state.hasComponent(eid, InventoryComponent)) return EMPTY_STACKS;
  return getStacks(state, eid);
}

export function getItemQty(state: State, eid: number, itemId: string): number {
  if (!state.hasComponent(eid, InventoryComponent)) return 0;
  let total = 0;
  for (const stack of getStacks(state, eid)) {
    if (stack.itemId === itemId) total += stack.qty;
  }
  return total;
}

// One stack per item type: an item occupies a single slot whose qty is capped
// by its ItemDef.maxStack. addItem tops up the existing stack (or opens one new
// slot for a brand-new item) and returns whatever does not fit, rather than
// spilling the overflow into additional slots. Capacity therefore bounds the
// number of distinct items carried.
export function addItem(
  state: State,
  eid: number,
  itemId: string,
  qty: number
): number {
  if (qty <= 0) return 0;
  if (!state.hasComponent(eid, InventoryComponent)) return qty;

  const maxStack = resolveMaxStack(state, itemId);
  const stacks = getStacks(state, eid);
  const capacity = InventoryComponent.capacity[eid];
  const requested = Math.floor(qty);

  const existing = stacks.find((s) => s.itemId === itemId);
  let added = 0;
  if (existing) {
    const room = Math.max(0, maxStack - existing.qty);
    added = Math.min(room, requested);
    existing.qty += added;
  } else if (stacks.length < capacity) {
    added = Math.min(maxStack, requested);
    stacks.push({ itemId, qty: added });
  }

  if (added > 0) {
    InventoryComponent.slots[eid] = stacks.length;
    bump(eid);
    pendingQueue(state).push({
      event: INVENTORY_ADDED,
      payload: { entity: eid, itemId, qty: added },
    });
  }
  return requested - added;
}

export function removeItem(
  state: State,
  eid: number,
  itemId: string,
  qty: number
): boolean {
  if (qty <= 0) return true;
  if (!state.hasComponent(eid, InventoryComponent)) return false;
  const requested = Math.floor(qty);
  if (getItemQty(state, eid, itemId) < requested) return false;

  const stacks = getStacks(state, eid);
  let remaining = requested;
  for (const stack of stacks) {
    if (remaining <= 0) break;
    if (stack.itemId !== itemId) continue;
    const take = Math.min(stack.qty, remaining);
    stack.qty -= take;
    remaining -= take;
  }

  for (let i = stacks.length - 1; i >= 0; i--) {
    if (stacks[i].qty <= 0) stacks.splice(i, 1);
  }

  InventoryComponent.slots[eid] = stacks.length;
  bump(eid);
  pendingQueue(state).push({
    event: INVENTORY_REMOVED,
    payload: { entity: eid, itemId, qty: requested },
  });
  return true;
}

export const InventoryEventBridgeSystem: System = {
  group: 'simulation',
  update(state: State): void {
    const queue = pendingEvents.get(state);
    if (queue && queue.length > 0) {
      for (const evt of queue) {
        emitEvent(state, evt.event, evt.payload);
      }
      queue.length = 0;
    }

    const table = stacksByEntity.get(state);
    if (table && table.size > 0) {
      for (const eid of Array.from(table.keys())) {
        if (
          !state.exists(eid) ||
          !state.hasComponent(eid, InventoryComponent)
        ) {
          table.delete(eid);
        }
      }
    }
  },
};

export interface InventoryStackData {
  itemId: string;
  qty: number;
}

export interface InventoryEntitySnapshot {
  capacity: number;
  stacks: InventoryStackData[];
}

export function getInventoryEntitySnapshot(
  state: State,
  eid: number
): InventoryEntitySnapshot | null {
  if (!state.hasComponent(eid, InventoryComponent)) return null;
  return {
    capacity: InventoryComponent.capacity[eid],
    stacks: getStacks(state, eid).map((s) => ({
      itemId: s.itemId,
      qty: s.qty,
    })),
  };
}

// Restores stacks verbatim and bypasses the maxStack/capacity clamping addItem
// applies: a snapshot is already a valid prior state.
export function applyInventoryEntitySnapshot(
  state: State,
  eid: number,
  data: InventoryEntitySnapshot
): void {
  InventoryComponent.capacity[eid] = data.capacity;
  const stacks = getStacks(state, eid);
  stacks.length = 0;
  for (const s of data.stacks) stacks.push({ itemId: s.itemId, qty: s.qty });
  InventoryComponent.slots[eid] = stacks.length;
  InventoryComponent.version[eid] = (InventoryComponent.version[eid] + 1) >>> 0;
}
