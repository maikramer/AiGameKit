# RPG-Inventory Plugin (context.md)

<!-- LLM:OVERVIEW -->

Carried item storage for an entity: discrete, stackable items such as bombs, potions, wood, and quest relics. Each item is identified by an `itemId` whose definition lives in the `rpg-core` `DataRegistry` under kind `item` (see `ItemDef`). `InventoryComponent` holds the typed-array front (slot count, capacity, mutation version); the actual stacks live in a per-`State` side-table (`Map<eid, ItemStack[]>`). Stack model: **one stack per `itemId`**, capped by `ItemDef.maxStack`. `addItem` tops up the existing stack, or opens one new slot for a brand-new item when `capacity` (max distinct items) allows, and **returns the overflow** rather than spilling into additional slots. `INVENTORY_ADDED` / `INVENTORY_REMOVED` events are queued and flushed once per frame by the bridge system. Distinct from **rpg-vault**, which holds plain numeric resource balances with no per-unit identity.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-inventory/
├── context.md     # This file
├── index.ts       # Public re-exports
├── plugin.ts      # InventoryPlugin (recipe + system + defaults)
├── components.ts  # InventoryComponent (typed-array front)
└── systems.ts     # addItem / removeItem / getInventory + bridge system + snapshots
```

## Scope

- **In-scope**: Per-entity item stacks with `maxStack` clamping, capacity-bounded distinct-item slots, add/remove/get queries, mutation `version` for UI change detection, save/load snapshots.
- **Out-of-scope**: Item definitions and the `DataRegistry` (see `rpg-core`; `ItemDef.maxStack` is read from there), numeric currencies/materials (see `rpg-vault`), loot routing (see `rpg-core` `applyLootResult`, which calls `addItem`), UI rendering.

## Entry Points

- **plugin.ts**: `InventoryPlugin` (recipe `Inventory`, `InventoryEventBridgeSystem`, defaults).
- **components.ts**: `InventoryComponent`.
- **systems.ts**: `addItem`, `removeItem`, `getInventory`, `getItemQty`, snapshots, bridge system.

## Dependencies

- **Internal**: Core ECS (`State`, `System`, `MAX_ENTITIES`), `rpg-core` barrel (`emitEvent`, `getDataRegistry`, `INVENTORY_ADDED`, `INVENTORY_REMOVED`) and `rpg-core/types` (`ItemDef`, `ItemStack`). Requires `RpgCorePlugin` registered so the event bus and `DataRegistry` exist.
- **External**: None.

<!-- LLM:REFERENCE -->

### Component

#### InventoryComponent

- `slots: ui32`: current number of distinct item stacks (mirrors side-table length).
- `capacity: ui8`: max distinct items the entity can carry (default `20`). Bounds the number of `itemId`s, not the total quantity.
- `version: ui32`: incremented on every mutation (`addItem`, `removeItem`, snapshot restore). Use for UI change detection: read before and after a frame.

The stacks themselves live in the per-`State` side-table.

### Stack semantics

- **One stack per `itemId`.** An item occupies a single slot whose qty is capped by its `ItemDef.maxStack`.
- `maxStack` resolution: `DataRegistry.get<ItemDef>('item', itemId)`. Falls back to `1` when the def is missing or `maxStack` is invalid (`< 1` or non-integer). **Register every item** or it will not stack.
- `addItem` tops up the existing stack up to `maxStack`, or opens one new slot for a brand-new item when `stacks.length < capacity`. The remainder that did not fit is returned as the overflow count. Overflow does **not** spill into additional slots.
- `removeItem` decrements across as many stacks of that `itemId` as needed, then drops emptied stacks. Returns `false` without mutating if the total held is less than requested.

### Public API

- `getInventory(state, eid) -> readonly ItemStack[]`: empty (frozen) array if the entity has no `InventoryComponent`.
- `getItemQty(state, eid, itemId) -> number`: sum across all stacks of that item.
- `addItem(state, eid, itemId, qty) -> number`: returns the **overflow** (qty not added). A return of `0` means everything fit. Mutations bump `version` and queue an `INVENTORY_ADDED` event with `{ entity, itemId, qty: added }`.
- `removeItem(state, eid, itemId, qty) -> boolean`: `true` on success. Mutations bump `version` and queue an `INVENTORY_REMOVED` event with `{ entity, itemId, qty: requested }`.

### System

#### InventoryEventBridgeSystem (group: `simulation`)

- Flushes the pending add/remove event queue (emitted via `emitEvent`), then clears it.
- Drops side-table entries for entities that no longer exist or no longer have an `InventoryComponent`.

Events are queued rather than emitted inline so a single frame's batch of `addItem` calls produces a coherent stream after simulation, and so destroyed entities cannot emit after teardown.

### Recipe

- **`<Inventory>`**: components: `['inventory']`. Defaults: `capacity = 20`, `slots = 0`, `version = 0`. No parser; configure capacity programmatically by writing `InventoryComponent.capacity[eid]` if you need a non-default value.

### Snapshots (save/load)

- `getInventoryEntitySnapshot(state, eid) -> InventoryEntitySnapshot | null`: `null` when the entity has no `InventoryComponent`, else `{ capacity, stacks: [{ itemId, qty }] }`.
- `applyInventoryEntitySnapshot(state, eid, data)`: restores stacks verbatim and **bypasses** the `maxStack`/`capacity` clamping that `addItem` applies (a snapshot is already a valid prior state). Bumps `version`; emits no events.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Register item definitions first (without this, every item stacks to 1):

```ts
import { getDataRegistry, InventoryPlugin } from 'vibegame';

withPlugin(InventoryPlugin);
// ...after runtime build:
const reg = getDataRegistry(state);
reg.register('item', 'potion', { id: 'potion', name: 'Potion', maxStack: 99, tags: ['consumable'] });
reg.register('item', 'bomb',   { id: 'bomb',   name: 'Bomb',   maxStack: 99, tags: [] });
```

Declarative inventory (capacity defaults to 20):

```xml
<Inventory></Inventory>
```

Add, query, and remove (matches the simple-rpg `main.ts` pattern):

```ts
import { addItem, getItemQty, removeItem, InventoryComponent } from 'vibegame';

const overflow = addItem(state, heroEid, 'bomb', 5); // 0 if all 5 fit
const have = getItemQty(state, heroEid, 'bomb');     // 5
const ok = removeItem(state, heroEid, 'bomb', 1);    // true

const v = InventoryComponent.version[heroEid];        // bumps on every change
```

Overflow handling when a stack is full:

```ts
// Suppose 'potion' maxStack is 99 and the hero already holds 99.
const left = addItem(state, heroEid, 'potion', 10); // returns 10 (nothing added)
```

Save and restore one entity's inventory:

```ts
import {
  getInventoryEntitySnapshot,
  applyInventoryEntitySnapshot,
} from 'vibegame';

const snap = getInventoryEntitySnapshot(state, heroEid);
if (snap) applyInventoryEntitySnapshot(state, heroEid, snap);
```

<!-- /LLM:EXAMPLES -->
