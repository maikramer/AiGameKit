import type { State } from '../../core';
import type { LootEntry, LootTable } from './types';
import { emitEvent, LOOT_DROPPED, LOOT_ROLLED } from './events';
import { getDataRegistry } from './registry';
// Specific files (not barrels) to keep this module's dependencies one-directional
// and avoid pulling rpg-inventory/rpg-vault barrels into a barrel cycle.
import { addItem } from '../rpg-inventory/systems';
import { addResource } from '../rpg-vault/components';

export interface LootResult {
  itemId?: string;
  resourceKind?: string;
  qty: number;
}

export type RngFn = () => number;

export const LOOT_TABLE_KIND = 'loot-table';

const DEFAULT_RNG: RngFn = Math.random;

function resolveTable(state: State, tableId: string): LootTable | undefined {
  return getDataRegistry(state).get<LootTable>(LOOT_TABLE_KIND, tableId);
}

function rollQty(rng: RngFn, qtyMin: number, qtyMax: number): number {
  const lo = Math.trunc(qtyMin);
  const hi = Math.trunc(qtyMax);
  if (hi <= lo) return Math.max(0, lo);
  const span = hi - lo + 1;
  return lo + Math.floor(rng() * span);
}

function pickEntry(
  rng: RngFn,
  entries: readonly LootEntry[]
): LootEntry | undefined {
  let total = 0;
  for (const e of entries) {
    if (e.weight > 0) total += e.weight;
  }
  if (total <= 0) return undefined;
  let r = rng() * total;
  let last: LootEntry | undefined;
  for (const e of entries) {
    if (e.weight <= 0) continue;
    last = e;
    r -= e.weight;
    if (r < 0) return e;
  }
  return last;
}

/**
 * Roll a loot table `rolls` times (defaults to `table.rolls`). Each roll is an
 * independent weighted selection over the table entries; the picked entry's
 * quantity is a uniform integer in `[qtyMin, qtyMax]`. An optional `rng`
 * injects a deterministic source for tests (defaults to `Math.random`).
 *
 * Loot entries never reference other tables (no recursion / nested tables).
 */
export function rollLoot(
  state: State,
  tableId: string,
  rolls?: number,
  rng: RngFn = DEFAULT_RNG
): LootResult[] {
  const table = resolveTable(state, tableId);
  if (!table || table.entries.length === 0) return [];
  const count = rolls ?? table.rolls;
  if (count <= 0) return [];
  const results: LootResult[] = [];
  for (let i = 0; i < count; i++) {
    const entry = pickEntry(rng, table.entries);
    if (!entry) continue;
    const result: LootResult = {
      qty: rollQty(rng, entry.qtyMin, entry.qtyMax),
    };
    if (entry.itemId !== undefined) result.itemId = entry.itemId;
    if (entry.resourceKind !== undefined)
      result.resourceKind = entry.resourceKind;
    results.push(result);
  }
  emitEvent(state, LOOT_ROLLED, { tableId, results });
  return results;
}

/**
 * Route a rolled result onto an entity: an `itemId` result is added to the
 * entity's inventory (rpg-inventory `addItem`); a `resourceKind` result is
 * added to its vault (rpg-vault `addResource`). A `LOOT_DROPPED` event fires
 * with `{ entity, itemId?, resourceKind?, qty }`.
 */
export function applyLootResult(
  state: State,
  eid: number,
  result: LootResult
): void {
  const qty = Math.max(0, Math.floor(result.qty));
  if (qty <= 0) return;
  if (result.itemId !== undefined) {
    addItem(state, eid, result.itemId, qty);
    emitEvent(state, LOOT_DROPPED, {
      entity: eid,
      itemId: result.itemId,
      qty,
    });
  } else if (result.resourceKind !== undefined) {
    addResource(state, eid, result.resourceKind, qty);
    emitEvent(state, LOOT_DROPPED, {
      entity: eid,
      resourceKind: result.resourceKind,
      qty,
    });
  }
}
