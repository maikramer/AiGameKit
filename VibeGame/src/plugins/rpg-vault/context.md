# RPG-Vault Plugin (context.md)

<!-- LLM:OVERVIEW -->

Numeric resource storage for an entity: currencies and harvest materials such as gold, wood, stone. Each resource kind is an open-ended string resolved at runtime to a stable index via `registerResourceKind`. The balances themselves live in a per-`State` side-table (`Map<eid, Map<resourceIndex, { amount, capacity }>>`) because resource kinds are not known at compile time and cannot be expressed as fixed typed-array fields. `VaultComponent` is a one-field presence marker (`active: ui8`) that keeps vaults queryable through the normal ECS query API. Mutations happen through `addResource` / `spendResource`, which emit `ECONOMY_GAINED` / `ECONOMY_SPENT` events synchronously. Distinct from **rpg-inventory**, which holds discrete, stackable _items_ (`ItemDef` with `maxStack`); the vault holds plain numeric _quantities_ with no per-unit identity.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-vault/
├── context.md     # This file
├── index.ts       # Public re-exports
├── plugin.ts      # RpgVaultPlugin (recipe + parser + system)
├── components.ts  # VaultComponent, all vault API functions, snapshots
└── systems.ts     # VaultEventBridgeSystem (prune destroyed entities)
```

## Scope

- **In-scope**: Per-entity numeric resource balances with per-kind capacity, open-ended resource-kind registration, economy events, save/load snapshots.
- **Out-of-scope**: Discrete item stacks (see `rpg-inventory`), item definitions (see `rpg-core` `ItemDef`), loot routing (see `rpg-core` `applyLootResult`, which calls `addResource`), UI rendering.

## Vault vs Inventory

| Concern       | rpg-vault                                         | rpg-inventory                                   |
| ------------- | ------------------------------------------------- | ----------------------------------------------- |
| Models        | Numeric balances (currencies, materials)          | Discrete item stacks                            |
| Unit identity | None (just an amount)                             | Per-item `itemId`, capped by `ItemDef.maxStack` |
| Kind set      | Open-ended strings, registered at runtime         | `item` defs in `DataRegistry`                   |
| Capacity      | Per-resource amount cap (`capacity-gold="99999"`) | Max distinct item slots                         |
| Events        | `ECONOMY_GAINED`, `ECONOMY_SPENT`                 | `INVENTORY_ADDED`, `INVENTORY_REMOVED`          |

Example: gold coins, wood, and stone go in the **vault** (fungible amounts). A bomb, a potion, or a quest relic goes in the **inventory** (discrete, stackable items).

## Entry Points

- **plugin.ts**: `RpgVaultPlugin` (recipe `Vault`, parser, `VaultEventBridgeSystem`).
- **components.ts**: `VaultComponent`, all balance API, snapshots.
- **systems.ts**: `VaultEventBridgeSystem`.

## Dependencies

- **Internal**: Core ECS (`State`, `System`, `MAX_ENTITIES`), `rpg-core/events` (`ECONOMY_GAINED`, `ECONOMY_SPENT`, `emitEvent`). Requires `RpgCorePlugin` (or `RpgCoreEventsPlugin`) to be registered so the event bus exists.
- **External**: None.

<!-- LLM:REFERENCE -->

### Component

#### VaultComponent

- `active: ui8` (1 while the entity owns at least one resource, 0 otherwise).

The balances themselves are not on the component; they live in the side-table described above. The marker exists so `defineQuery([VaultComponent])` finds vault-holding entities.

### Resource kinds

Resource kinds are arbitrary strings (`'gold'`, `'wood'`, `'stone'`, or anything game-specific). `registerResourceKind(state, kind)` assigns a stable numeric index (idempotent; re-registering returns the same index). `getResourceKindIndex(state, kind)` returns `undefined` for an unregistered kind.

### Public API

- `registerResourceKind(state, kind) -> number`
- `getResourceKindIndex(state, kind) -> number | undefined`
- `getResource(state, eid, kind) -> number` (0 if kind/slot absent)
- `getCapacity(state, eid, kind) -> number` (defaults to `DEFAULT_VAULT_CAPACITY = 9999`)
- `setCapacity(state, eid, kind, capacity)`: clamps the current amount down to the new cap. Implicitly registers the kind.
- `addResource(state, eid, kind, amount)`: no-op for `amount <= 0`; otherwise clamps to capacity, sets `active = 1`, emits `ECONOMY_GAINED` with `{ entity, kind, amount: gained }` (the delta after clamping, so no event fires when the cap is already reached).
- `spendResource(state, eid, kind, amount) -> boolean`: returns `false` (no mutation) if the kind is unknown or the balance is short; otherwise subtracts and emits `ECONOMY_SPENT` with `{ entity, kind, amount }`.

### System

#### VaultEventBridgeSystem (group: `simulation`)

- Drops side-table entries for destroyed entities (`pruneVaults`). The economy events themselves are emitted synchronously inside `addResource` / `spendResource` (callers can rely on handlers firing immediately after the call), so this system owns only the per-frame lifecycle.

### Recipe

- **`<Vault>`**: components: `['vault']` (default `active = 1`). The parser reads every attribute named `capacity-<kind>` and calls `setCapacity(state, entity, kind, Number(value))`. Empty kind names and non-numeric values are skipped silently. Unknown attributes are ignored.

### Snapshots (save/load)

- `getVaultEntitySnapshot(state, eid) -> VaultEntitySnapshot | null`: returns `null` (skip) when the entity owns no resources, else `{ resources: { [kind]: { amount, capacity } } }`.
- `applyVaultEntitySnapshot(state, eid, data)`: restores the side-table verbatim, sets `active = 1`, emits **no** economy events (a save restore is not gameplay).

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Declarative vault with a gold cap (parser applies `capacity-gold`):

```xml
<Vault capacity-gold="99999" capacity-wood="9999"></Vault>
```

Programmatic setup and mutations:

```ts
import {
  RpgVaultPlugin,
  registerResourceKind,
  addResource,
  spendResource,
  getResource,
  setCapacity,
} from 'vibegame';

withPlugin(RpgVaultPlugin);
// ...after runtime build, for the hero entity:
registerResourceKind(state, 'gold');
addResource(state, heroEid, 'gold', 250);          // emits ECONOMY_GAINED
addResource(state, heroEid, 'wood', 10);           // auto-registers 'wood'
setCapacity(state, heroEid, 'stone', 999);         // auto-registers 'stone'

const ok = spendResource(state, heroEid, 'gold', 50); // true, emits ECONOMY_SPENT
const balance = getResource(state, heroEid, 'gold');  // 200
```

Save and restore a single entity's resources:

```ts
import {
  getVaultEntitySnapshot,
  applyVaultEntitySnapshot,
} from 'vibegame';

const snap = getVaultEntitySnapshot(state, heroEid); // null if nothing stored
if (snap) applyVaultEntitySnapshot(state, heroEid, snap);
```

<!-- /LLM:EXAMPLES -->
