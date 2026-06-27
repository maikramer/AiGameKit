# RPG-Core Plugin (context.md)

<!-- LLM:OVERVIEW -->

Data foundation for the RPG plugin family. Owns three singletons, one per `State`: a generic **`DataRegistry`** (`(kind, id) -> def` store loaded from YAML/JSON), a synchronous **`EventBus`** (pub/sub with per-entity auto-cleanup), and the **loot** helpers (`rollLoot`, `applyLootResult`) that route rolled results into `rpg-inventory` (items) and `rpg-vault` (resources). Exports two plugins: `RpgCorePlugin` (recipes `RpgData`/`LootTable` + parsers + registry/eventbus init) and the smaller `RpgCoreEventsPlugin` (event bus only). The registry holds arbitrary kinds; the canonical ones are `item` (read by `rpg-inventory` to resolve `ItemDef.maxStack`), `loot-table` (read by `rollLoot`), plus game-specific kinds such as `melee-ai`, `skill`, `statusEffect`. Definitions are plain JSON-serializable objects; the registry injects an `id` field equal to the key when the def does not already declare one.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-core/
├── context.md     # This file
├── index.ts       # Public re-exports
├── plugin.ts      # RpgCorePlugin, RpgCoreEventsPlugin (recipes + parsers)
├── registry.ts    # DataRegistry class + getDataRegistry(state)
├── events.ts      # EventBus class, event-name constants, EventBusCleanupSystem
├── loot.ts        # rollLoot, applyLootResult, LootResult, LOOT_TABLE_KIND
└── types.ts       # ItemDef, LootTable, SkillDef, StatusEffectDef, ... (pure types)
```

## Scope

- **In-scope**: Generic data registry (YAML/JSON ingest, programmatic register/get), synchronous event bus, loot rolling and routing, shared RPG type definitions.
- **Out-of-scope**: Item stack storage (see `rpg-inventory`), resource/currency storage (see `rpg-vault`), AI tuning consumption (see `rpg-ai`), save serialization wiring (see `save-load`; snapshots are exposed by the storage plugins).

## Entry Points

- **plugin.ts**: `RpgCorePlugin` (recipes + parsers + `initialize`), `RpgCoreEventsPlugin` (event bus only).
- **registry.ts**: `DataRegistry`, `getDataRegistry(state)`.
- **events.ts**: `EventBus`, `getEventBus(state)`, `emitEvent`, `onEvent`, event-name constants.
- **loot.ts**: `rollLoot`, `applyLootResult`, `LOOT_TABLE_KIND`.
- **index.ts**: Re-exports.

## Dependencies

- **Internal**: Core ECS (`State`, `System`), `../../core/utils/logger`. `loot.ts` imports `addItem` from `rpg-inventory/systems` and `addResource` from `rpg-vault/components` directly (file-level imports, not barrels, to avoid a cycle).
- **External**: `yaml` (`parse`), and either the Bun runtime (`globalThis.Bun.Glob` + `Bun.file`) or Node `node:fs` for filesystem loading. Both are acquired lazily so browser bundles never resolve them.

<!-- LLM:REFERENCE -->

### DataRegistry

Per-`State` singleton (WeakMap). Document shape for YAML/JSON ingest: `{ <kind>: { <id>: <def> } }`. The top-level key becomes the kind verbatim (prefer singular: `item`, `skill`, `statusEffect`).

- `register<T>(kind, id, def)`: overwrite-safe insert into the kind bucket.
- `get<T>(kind, id)`, `has(kind, id)`, `all<T>(kind)` (readonly array of defs), `kinds()`, `clear()`.
- `loadYaml(text)`, `loadJson(text)`: parse then `ingest`; empty YAML (`null`) is a no-op.
- `loadDirectory(dir)`: async; reads every `*.{yaml,yml,json}` directly under `dir` (non-recursive). Requires the Bun runtime; throws in the browser.

### EventBus

Synchronous pub/sub. Handlers fire in subscription order during `emit`; exceptions are caught and logged (they do not stop sibling handlers). `once` unsubscribes before invoking. `on`/`once` accept `SubscriptionOptions.entityRef` so the cleanup system auto-removes the sub once that entity is destroyed.

### System

#### EventBusCleanupSystem (group: `simulation`)

- Each frame, drops subscriptions whose `entityRef` no longer exists. Lets gameplay code subscribe with `onEvent(..., { entityRef })` without tracking manual `off` calls on death.

### Recipes & Parsers

- **`<RpgData src="…">`**: declarative loader. `src` is a single `.yaml`/`.yml`/`.json` file path. Creates a marker entity with no components purely so its parser runs. The parser reads the file **synchronously** via `node:fs` (`readFileSync`) because the engine `Parser` contract is sync and `Bun.file` has no sync API. Logs an error in the browser (no `node:fs`); use `getDataRegistry().loadYaml/loadJson/loadDirectory` programmatically there.
- **`<LootTable src="…" id="…">`**: convenience over `RpgData`. Loads a file whose top-level key is `loot-table`. The `id` attribute is documentation only (a file may declare many tables); it has no runtime effect.

### Loot

`rollLoot(state, tableId, rolls?, rng = Math.random)`: resolves `loot-table/<tableId>` in the registry, then runs `rolls` (default `table.rolls`) independent weighted selections. Each entry's quantity is a uniform integer in `[qtyMin, qtyMax]`. No nested tables, no recursion. Emits `LOOT_ROLLED` with `{ tableId, results }`.

`applyLootResult(state, eid, result)`: routes a single `LootResult`: `itemId` results call `addItem` (rpg-inventory); `resourceKind` results call `addResource` (rpg-vault). Emits `LOOT_DROPPED` with `{ entity, itemId?, resourceKind?, qty }`.

### Event-name constants

`COMBAT_DAMAGED`, `COMBAT_HEALED`, `COMBAT_KILLED`, `COMBAT_DEATH`, `ECONOMY_SPENT`, `ECONOMY_GAINED`, `INVENTORY_ADDED`, `INVENTORY_REMOVED`, `PROGRESSION_LEVEL_UP`, `PROGRESSION_XP_GAINED`, `PROGRESSION_SKILL_PURCHASED`, `STATUS_APPLIED`, `STATUS_EXPIRED`, `STATUS_CANCELLED`, `LOOT_ROLLED`, `LOOT_DROPPED`.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Register `ItemDef`s programmatically (the simple-rpg `main.ts` pattern; without a registered `ItemDef`, `rpg-inventory` caps every stack at 1):

```ts
import { getDataRegistry, withPlugin, RpgCorePlugin } from 'vibegame';

withPlugin(RpgCorePlugin);
// ...after runtime build:
const reg = getDataRegistry(state);
for (const [id, name, icon] of [
  ['bomb', 'Bomb', 'bomb-icon'],
  ['wood', 'Wood', 'wood-icon'],
  ['potion', 'Potion', 'potion-icon'],
] as const) {
  reg.register('item', id, { id, name, icon, maxStack: 99, tags: [] });
}
```

Ingest YAML at runtime (also from simple-rpg `main.ts`, loading AI presets fetched over HTTP):

```ts
const reg = getDataRegistry(state);
const res = await fetch('/data/ai/goblin.yaml');
if (res.ok) reg.loadYaml(await res.text());
```

Declarative load from a scene file (Node/Bun runtime only, sync `readFileSync`):

```xml
<RpgData src="data/items.yaml"></RpgData>
<LootTable src="data/loot.yaml" id="goblin-drops"></LootTable>
```

A loot table YAML and a roll:

```yaml
loot-table:
  goblin-drops:
    rolls: 2
    entries:
      - { itemId: gold_purse, qtyMin: 5, qtyMax: 12, weight: 70 }
      - { resourceKind: gold, qtyMin: 8, qtyMax: 18, weight: 30 }
```

```ts
import { rollLoot, applyLootResult } from 'vibegame';
const drops = rollLoot(state, 'goblin-drops');
for (const r of drops) applyLootResult(state, heroEid, r);
```

Subscribe with auto-cleanup on entity death:

```ts
import { onEvent, COMBAT_DAMAGED } from 'vibegame';
onEvent(state, COMBAT_DAMAGED, (p) => { /* ... */ }, { entityRef: heroEid });
```

<!-- /LLM:EXAMPLES -->
