# RPG Economy Plugin (context.md)

<!-- LLM:OVERVIEW -->

Currency trades and price tables that compose the rpg-inventory and rpg-vault plugins into atomic buy/sell operations. The plugin owns no ECS component of its own: gold is a vault resource (`GOLD_KIND = 'gold'`) and items live in inventories. The core is `transact`, an atomic payer/payee swap that validates every failure mode read-only first (insufficient gold, insufficient stock, no inventory room accounting for `maxStack` and `capacity`) and only then mutates, so a rejected trade leaves both sides untouched. `buyItem` frames the buyer as payer; `sellItem` is the mirror framing where the named buyer pays. Prices come from `PriceEntry` rows (`{ buy, sell }`) registered under the `price` kind in the rpg-core data registry, read via `getPrice(state, itemId, 'buy' | 'sell')`. The declarative `<PriceTable src="...">` recipe loads a YAML file into the registry at parse time using a lazy, browser-safe `node:fs` shim (it logs an error and skips in the browser, where filesystem access is unavailable). The plugin has no events of its own: vault already emits `ECONOMY_GAINED` / `ECONOMY_SPENT` synchronously and inventory's own bridge drains `INVENTORY_ADDED` / `INVENTORY_REMOVED`, so `EconomyEventBridgeSystem` is a reserved no-op kept for future composite events.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-economy/
├── context.md       # This file
├── plugin.ts        # EconomyPlugin (PriceTable recipe + YAML loader)
├── systems.ts       # transact/buy/sell/getPrice + EconomyEventBridgeSystem
└── index.ts         # Public re-exports
```

## Scope

- **In-scope**: Atomic buy/sell between two entities, price lookup from the registry, `maxStack`/`capacity` validation, declarative `<PriceTable>` YAML loader, gold as a vault resource.
- **Out-of-scope**: Storing gold or items (delegated to rpg-vault and rpg-inventory), UI/shop windows, price negotiation, dynamic pricing, emitting economy events (vault and inventory already do).

## Entry Points

- **plugin.ts**: `EconomyPlugin` definition (recipe + parser, no components, no initialize).
- **systems.ts**: `PriceEntry` type, `GOLD_KIND`, `getPrice`, `buyItem`, `sellItem`, and the reserved bridge system.
- **index.ts**: Public re-exports (also re-exported from the `vibegame` barrel).

## Dependencies

- **Internal**: rpg-core (`getDataRegistry`, type `ItemDef`), rpg-inventory (`InventoryComponent`, `addItem`, `getInventory`, `getItemQty`, `removeItem`), rpg-vault (`addResource`, `getResource`, `spendResource`), core ECS (`State`).
- **External**: `node:fs` via a lazy `acquireNodeFs` shim (browser-safe; mirrors rpg-core). Only the `<PriceTable>` loader uses it.

## Public API

- `GOLD_KIND` (`'gold'`), `PriceKind` (`'buy' | 'sell'`), `PriceEntry` (`{ buy: number; sell: number }`).
- `getPrice(state, itemId, kind): number` (0 if no entry or invalid value).
- `buyItem(state, buyer, seller, itemId, qty, pricePerUnit): boolean`.
- `sellItem(state, seller, buyer, itemId, qty, pricePerUnit): boolean`.
- `EconomyPlugin`, `EconomyEventBridgeSystem`.

<!-- LLM:REFERENCE -->

### Component

None. Economy owns no ECS component. Gold lives in rpg-vault resources and items in rpg-inventory stacks.

### System

#### EconomyEventBridgeSystem

- Group: `simulation`.
- Currently a no-op reserved hook. Vault emits `ECONOMY_GAINED` / `ECONOMY_SPENT` synchronously and inventory's own bridge drains `INVENTORY_ADDED` / `INVENTORY_REMOVED` each step.

### Recipe

- **PriceTable**: parserAttributes `['src']`, no components. The parser reads the YAML file at `src` via the lazy `node:fs` shim and loads it into the rpg-core data registry with `loadYaml`. In the browser (no fs) it logs an error and skips. Failed reads log a warning and skip.

### Transação atômica

- `transact(payer, payee, itemId, qty, pricePerUnit)` valida antes de mutar: `getResource(payer, 'gold') >= total`, `getItemQty(payee, itemId) >= n`, `canAddItem(payer, ...)` (respeita `maxStack` do `ItemDef` e `capacity` do inventório). Só depois executa `spendResource`/`addResource` (gold) e `removeItem`/`addItem` (item). Rejeição não altera nenhum lado.
- `buyItem(buyer, seller, ...)` chama `transact(buyer, seller, ...)` (buyer paga gold, recebe item). `sellItem(seller, buyer, ...)` chama `transact(buyer, seller, ...)` (o `buyer` informado paga; framing espelhado de `buyItem`).
- Preços vêm de entradas `PriceEntry` (`{ buy, sell }`) no kind `price` do registry, lidas por `getPrice`.

### Systems (ordem no plugin)

1. **EconomyEventBridgeSystem** (`simulation`): reservado, sem efeito hoje.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Load prices from YAML at scene parse time:

```xml
<PriceTable src="assets/data/prices.yaml"></PriceTable>
```

A YAML row registers a price entry (consumed via the `price` registry kind):

```yaml
price:
  health_potion:
    buy: 25
    sell: 10
```

Buy and sell from code:

```ts
import { buyItem, sellItem, getPrice } from 'vibegame';

const unit = getPrice(state, 'health_potion', 'buy'); // 25
const ok = buyItem(state, heroEid, shopEid, 'health_potion', 3, unit); // false if not enough gold/stock/room
sellItem(state, heroEid, shopEid, 'longsword', 1, getPrice(state, 'longsword', 'sell'));
```

A rejected trade is atomic: gold and inventory are unchanged on either side.

<!-- /LLM:EXAMPLES -->

## Known Limitations

- No own component or events; gold and items are owned by rpg-vault and rpg-inventory. Both plugins must be registered for trades to work.
- `<PriceTable>` filesystem loading is unavailable in the browser; it logs and skips. Use the builder/data API to register `PriceEntry` rows at runtime in browser builds.
- Prices default to 0 when a `price` entry is missing or non-finite; callers should guard against free trades.
- `qty` is floored; values of 0 or less always reject.
- Single-currency model (`GOLD_KIND`). Multi-currency support would need additional vault resource kinds, not changes here.
