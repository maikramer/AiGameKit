import { MAX_ENTITIES } from '../../core/ecs/constants';
import type { State } from '../../core';
import { ECONOMY_GAINED, ECONOMY_SPENT, emitEvent } from '../rpg-core/events';

export const DEFAULT_VAULT_CAPACITY = 9999;

interface VaultSlot {
  amount: number;
  capacity: number;
}

interface VaultState {
  readonly table: Map<number, Map<number, VaultSlot>>;
  readonly kindToIndex: Map<string, number>;
  readonly indexToKind: string[];
}

const vaultStates = new WeakMap<State, VaultState>();

function getVaultState(state: State): VaultState {
  let vs = vaultStates.get(state);
  if (!vs) {
    vs = { table: new Map(), kindToIndex: new Map(), indexToKind: [] };
    vaultStates.set(state, vs);
  }
  return vs;
}

// Presence marker — an entity with a vault. The balances themselves live in a
// per-State side-table (Map<eid, Map<resourceIndex, {amount,capacity}>>) because
// resource kinds are open-ended strings resolved at runtime via registerResourceKind,
// which cannot be expressed as fixed typed-array fields. The marker keeps vaults
// queryable (defineQuery([VaultComponent])) and SOA-compatible as a registered component.
export const VaultComponent = {
  active: new Uint8Array(MAX_ENTITIES),
} as const;

export function registerResourceKind(state: State, kind: string): number {
  const vs = getVaultState(state);
  const existing = vs.kindToIndex.get(kind);
  if (existing !== undefined) return existing;
  const idx = vs.indexToKind.length;
  vs.kindToIndex.set(kind, idx);
  vs.indexToKind.push(kind);
  return idx;
}

export function getResourceKindIndex(
  state: State,
  kind: string
): number | undefined {
  return getVaultState(state).kindToIndex.get(kind);
}

function ensureSlot(
  state: State,
  eid: number,
  resourceIndex: number
): VaultSlot {
  const vs = getVaultState(state);
  let perEntity = vs.table.get(eid);
  if (!perEntity) {
    perEntity = new Map();
    vs.table.set(eid, perEntity);
  }
  let slot = perEntity.get(resourceIndex);
  if (!slot) {
    slot = { amount: 0, capacity: DEFAULT_VAULT_CAPACITY };
    perEntity.set(resourceIndex, slot);
  }
  return slot;
}

function peekSlot(
  state: State,
  eid: number,
  resourceIndex: number
): VaultSlot | undefined {
  return getVaultState(state).table.get(eid)?.get(resourceIndex);
}

export function getResource(state: State, eid: number, kind: string): number {
  const idx = getResourceKindIndex(state, kind);
  if (idx === undefined) return 0;
  return peekSlot(state, eid, idx)?.amount ?? 0;
}

export function getCapacity(state: State, eid: number, kind: string): number {
  const idx = getResourceKindIndex(state, kind);
  if (idx === undefined) return DEFAULT_VAULT_CAPACITY;
  return peekSlot(state, eid, idx)?.capacity ?? DEFAULT_VAULT_CAPACITY;
}

export function setCapacity(
  state: State,
  eid: number,
  kind: string,
  capacity: number
): void {
  const idx = registerResourceKind(state, kind);
  const slot = ensureSlot(state, eid, idx);
  slot.capacity = capacity;
  if (slot.amount > capacity) slot.amount = capacity;
}

export function addResource(
  state: State,
  eid: number,
  kind: string,
  amount: number
): void {
  if (amount <= 0) return;
  const idx = registerResourceKind(state, kind);
  const slot = ensureSlot(state, eid, idx);
  const before = slot.amount;
  slot.amount = Math.min(before + amount, slot.capacity);
  const gained = slot.amount - before;
  VaultComponent.active[eid] = 1;
  if (gained > 0) {
    emitEvent(state, ECONOMY_GAINED, { entity: eid, kind, amount: gained });
  }
}

export function spendResource(
  state: State,
  eid: number,
  kind: string,
  amount: number
): boolean {
  if (amount <= 0) return true;
  const idx = getResourceKindIndex(state, kind);
  if (idx === undefined) return false;
  const slot = peekSlot(state, eid, idx);
  if (!slot || slot.amount < amount) return false;
  slot.amount -= amount;
  emitEvent(state, ECONOMY_SPENT, { entity: eid, kind, amount });
  return true;
}

export function pruneVaults(
  state: State,
  exists: (eid: number) => boolean
): number {
  const table = getVaultState(state).table;
  let removed = 0;
  for (const eid of Array.from(table.keys())) {
    if (!exists(eid)) {
      table.delete(eid);
      removed++;
    }
  }
  return removed;
}
