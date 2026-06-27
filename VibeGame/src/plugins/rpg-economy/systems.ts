import type { State, System } from '../../core';
import { getDataRegistry } from '../rpg-core';
import type { ItemDef } from '../rpg-core/types';
import {
  InventoryComponent,
  addItem,
  getInventory,
  getItemQty,
  removeItem,
} from '../rpg-inventory';
import { addResource, getResource, spendResource } from '../rpg-vault';

export const GOLD_KIND = 'gold';

export type PriceKind = 'buy' | 'sell';

export interface PriceEntry {
  readonly buy: number;
  readonly sell: number;
}

export function getPrice(
  state: State,
  itemId: string,
  kind: PriceKind
): number {
  const entry = getDataRegistry(state).get<PriceEntry>('price', itemId);
  if (!entry) return 0;
  const value = entry[kind];
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function resolveMaxStack(state: State, itemId: string): number {
  const def = getDataRegistry(state).get<ItemDef>('item', itemId);
  if (!def || typeof def.maxStack !== 'number' || def.maxStack < 1) return 1;
  return Math.floor(def.maxStack);
}

function canAddItem(
  state: State,
  eid: number,
  itemId: string,
  qty: number
): boolean {
  if (qty <= 0) return true;
  if (!state.hasComponent(eid, InventoryComponent)) return false;
  const maxStack = resolveMaxStack(state, itemId);
  const capacity = InventoryComponent.capacity[eid];
  const stacks = getInventory(state, eid);
  const existing = stacks.find((s) => s.itemId === itemId);
  if (existing) return qty <= maxStack - existing.qty;
  if (stacks.length >= capacity) return false;
  return qty <= maxStack;
}

// Atomicity: all failure modes (insufficient gold, insufficient stock, no
// inventory room) are validated read-only above, BEFORE any mutation below.
// The apply steps are therefore guaranteed to succeed, so a rejection leaves
// both payer and payee untouched.
function transact(
  state: State,
  payer: number,
  payee: number,
  itemId: string,
  qty: number,
  pricePerUnit: number
): boolean {
  const n = Math.floor(qty);
  if (n <= 0) return false;
  const total = Math.max(0, pricePerUnit) * n;

  if (getResource(state, payer, GOLD_KIND) < total) return false;
  if (getItemQty(state, payee, itemId) < n) return false;
  if (!canAddItem(state, payer, itemId, n)) return false;

  spendResource(state, payer, GOLD_KIND, total);
  addResource(state, payee, GOLD_KIND, total);
  removeItem(state, payee, itemId, n);
  addItem(state, payer, itemId, n);
  return true;
}

export function buyItem(
  state: State,
  buyer: number,
  seller: number,
  itemId: string,
  qty: number,
  pricePerUnit: number
): boolean {
  return transact(state, buyer, seller, itemId, qty, pricePerUnit);
}

export function sellItem(
  state: State,
  seller: number,
  buyer: number,
  itemId: string,
  qty: number,
  pricePerUnit: number
): boolean {
  return transact(state, buyer, seller, itemId, qty, pricePerUnit);
}

// Reserved simulation-group hook. Economy has no events of its own: Vault
// already emits ECONOMY_GAINED/SPENT synchronously and Inventory's own bridge
// drains INVENTORY_ADDED/REMOVED each step. Kept for future composite events.
export const EconomyEventBridgeSystem: System = {
  group: 'simulation',
  update(_state: State): void {},
};
