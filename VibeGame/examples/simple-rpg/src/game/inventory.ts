// Inventory model. Two kinds of entries:
//  - "resources" are live views over existing counters (gold/wood/stone) so the
//    bag never desyncs from the HUD chips or the merchant's spend logic.
//  - "items" are owned here (a small store) for things the bag itself holds,
//    ready for future loot/consumables.

export interface ItemName {
  en: string;
  pt: string;
}

export interface InventoryEntry {
  id: string;
  icon: string;
  name: ItemName;
  qty: number;
}

export const INVENTORY_CAPACITY = 20;

type Provider = () => number;

interface ResourceSlot {
  id: string;
  icon: string;
  name: ItemName;
  getQty: Provider;
}

const resources: ResourceSlot[] = [];
const items: Record<string, { icon: string; name: ItemName; qty: number }> = {};
let version = 0;

/** Expose an existing counter (gold/wood/stone) as a bag entry. Idempotent. */
export function registerResource(
  id: string,
  icon: string,
  name: ItemName,
  getQty: Provider
): void {
  if (resources.some((r) => r.id === id)) return;
  resources.push({ id, icon, name, getQty });
  version += 1;
}

/** Declare a bag-owned item type (qty starts at 0). */
export function defineItem(id: string, icon: string, name: ItemName): void {
  if (!items[id]) items[id] = { icon, name, qty: 0 };
}

export function addItem(id: string, n = 1): void {
  const it = items[id];
  if (!it) return;
  it.qty += n;
  version += 1;
}

export function removeItem(id: string, n = 1): void {
  const it = items[id];
  if (!it) return;
  it.qty = Math.max(0, it.qty - n);
  version += 1;
}

export function getItemQty(id: string): number {
  return items[id]?.qty ?? 0;
}

/** Bumps on bag-owned item changes (resource changes are read live on demand). */
export function getInventoryVersion(): number {
  return version;
}

/** Current non-empty entries: live resources first, then owned items. */
export function getInventory(): InventoryEntry[] {
  const out: InventoryEntry[] = [];
  for (const r of resources) {
    const q = r.getQty();
    if (q > 0) out.push({ id: r.id, icon: r.icon, name: r.name, qty: q });
  }
  for (const id in items) {
    const it = items[id];
    if (it.qty > 0) out.push({ id, icon: it.icon, name: it.name, qty: it.qty });
  }
  return out;
}
