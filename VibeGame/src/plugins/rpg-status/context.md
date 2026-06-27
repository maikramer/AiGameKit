# RPG Status Plugin (context.md)

<!-- LLM:OVERVIEW -->

Temporary status effects (buffs, poison, heal-over-time) with a full apply, tick, expire, cancel lifecycle. Active effects are kept in side tables keyed by `WeakMap<State, ...>` as `ActiveStatusEffect[]` per entity; the ECS `status-effect` component only mirrors `count` and a `version` counter so systems and HUDs can query and detect changes. `applyStatus` resolves a `StatusEffectDef` from the rpg-core registry, attaches the component (registering an `onDestroy` cleanup), and honors three stack modes: `replace` (reset timer and modifiers), `stack` (add duration), `max` (keep the longer duration). `tickStatusEffects` decrements remaining time each frame, fires the def's `tickEffect` (an `event-trigger` that re-emits as a typed event such as `status:damage` or `status:heal`) at `tickInterval`, and splices expired entries emitting `STATUS_EXPIRED`. Manual removal goes through `cancelStatus` / `cancelAllStatuses` (`STATUS_CANCELLED`). Effects are wiped automatically on entity destruction (the `onDestroy` hook) and on `COMBAT_DEATH` (a one-time subscription in `ensureDeathSubscription`). Three built-in defs ship by default: `speed-buff`, `heal-over-time`, `poison`. Like other rpg plugins, mutations push to a `pendingEvents` queue that `StatusEffectEventBridgeSystem` flushes to the event bus each simulation step.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-status/
├── context.md       # This file
├── plugin.ts        # StatusEffectsPlugin (recipe + parser + default defs)
├── components.ts    # StatusEffectComponent + apply/tick/cancel API
├── systems.ts       # StatusEffectTickSystem + StatusEffectEventBridgeSystem
└── index.ts         # Public re-exports
```

## Scope

- **In-scope**: Apply, tick, expire, cancel status effects; stack modes; per-def tick intervals and tick effects; modifier aggregation; death and entity-removal cleanup; save/load snapshots; declarative `<StatusApplication>` recipe.
- **Out-of-scope**: Damage/heal application (the tick effect emits an event a combat/health plugin consumes), status def authoring beyond the built-in defaults (defs live in the rpg-core registry), visual FX per effect.

## Entry Points

- **plugin.ts**: `StatusEffectsPlugin` definition (systems, recipe, parser, initialize registers defaults and the death subscription).
- **components.ts**: `StatusEffectComponent` plus the apply/tick/cancel/query API and snapshots.
- **index.ts**: Public re-exports (also re-exported from the `vibegame` barrel).

## Dependencies

- **Internal**: rpg-core (`getDataRegistry`, `getEventBus`, `onEvent`, event constants `STATUS_APPLIED` / `STATUS_EXPIRED` / `STATUS_CANCELLED` / `COMBAT_DEATH`, types `StatusEffectDef` / `StatModifier` / `SkillEffect`), core ECS (`State`, `MAX_ENTITIES`).
- **External**: none beyond the engine core.

## Public API

- `applyStatus(state, eid, defId, options?: { stackMode? })`, `cancelStatus(state, eid, defId)`, `cancelAllStatuses(state, eid)`.
- `getActiveStatuses(state, eid)`, `getStatusModifiers(state, eid): StatModifier[]`.
- `tickStatusEffects(state, dt)` (advanced; normally driven by the tick system).
- `ensureDeathSubscription(state)` (idempotent; wired in initialize and tick).
- `getStatusEffectEntitySnapshot` / `applyStatusEffectEntitySnapshot` (save/load).
- `drainPendingEvents` (consumed by the bridge system).

<!-- LLM:REFERENCE -->

### Component

#### StatusEffectComponent

- count: ui8 (number of active effects on the entity)
- version: ui32 (bumped on every add/cancel/expire/tick recalculation; wraps at 32 bits; use for UI change detection)

The full per-effect state (`defId`, `remainingTime`, `tickElapsed`, `modifiers`) lives in side tables, not in ECS arrays.

### System

#### StatusEffectTickSystem

- Group: `simulation`.
- Ensures the death subscription, then calls `tickStatusEffects(state, deltaTime)` when `dt > 0`.

#### StatusEffectEventBridgeSystem

- Group: `simulation`.
- Drains `pendingEvents` and emits each on the rpg-core event bus.

### Recipe

- **StatusApplication**: components `['status-effect']`, parserAttributes `status` and `target`. The parser applies the named status to the entity only when `target` is `self` (or omitted). Missing or empty `status` is ignored.

### Ciclo de vida do efeito

1. **Apply** (`applyStatus`): resolve o `StatusEffectDef`, anexa o componente (com cleanup `onDestroy`), aplica o `stackMode` (`replace` / `stack` / `max`), recalcula `count`/`version` e enfileira `STATUS_APPLIED`.
2. **Tick** (`tickStatusEffects`, via `StatusEffectTickSystem`): decrementa `remainingTime`; a cada `tickInterval` dispara o `tickEffect` (`event-trigger` reemitido como `status:damage`, `status:heal`, etc., com `{ ...payload, eid, defId }`).
3. **Expire**: quando `remainingTime <= 0`, remove o efeito e enfileira `STATUS_EXPIRED`.
4. **Cancel** manual: `cancelStatus` / `cancelAllStatuses` enfileiram `STATUS_CANCELLED` por efeito removido.
5. **Cancel-on-removal**: `onDestroy` (registro em `ensureComponent`) apaga a lista ativa da entidade; `COMBAT_DEATH` (assinatura única em `ensureDeathSubscription`) chama `cancelAllStatuses` no alvo.

### Systems (ordem no plugin)

1. **StatusEffectTickSystem** (`simulation`).
2. **StatusEffectEventBridgeSystem** (`simulation`).

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Apply poison to an entity on contact, declaratively on self:

```xml
<StatusApplication status="poison" target="self"></StatusApplication>
```

From code, with explicit stacking and cleanup hooks:

```ts
import { applyStatus, cancelStatus, getStatusModifiers } from 'vibegame';
import { onEvent, STATUS_EXPIRED, STATUS_APPLIED } from 'vibegame';

applyStatus(state, enemyEid, 'poison', { stackMode: 'stack' });
applyStatus(state, enemyEid, 'speed-buff'); // replace by default
const mods = getStatusModifiers(state, enemyEid); // [{ stat: 'speed', magnitude: 1.3, ... }]

onEvent(state, STATUS_EXPIRED, ({ eid, defId }) => { /* buff wore off */ });
onEvent(state, STATUS_APPLIED, ({ eid, defId, stackMode }) => { /* ... */ });
```

Built-in defs (registered in initialize): `speed-buff` (10s, speed x1.3), `heal-over-time` (6s, tick 2s, emits `status:heal` amount 5), `poison` (10s, tick 1s, emits `status:damage` amount 2).

<!-- /LLM:EXAMPLES -->

## Known Limitations

- Tick effects are `event-trigger` only; a combat/health plugin must handle `status:damage` / `status:heal` for actual HP changes.
- `applyStatus` silently no-ops if the def id is not registered; register custom defs in the rpg-core data registry first.
- Active effect data lives in side tables (`WeakMap<State, ...>`), so it is not visible to ECS queries that read raw typed arrays. Use `count` / `version` or `getActiveStatuses`.
- Events are flushed one simulation step after the mutation that queued them (bridge flush), not synchronously.
- Snapshot restore re-resolves modifiers from the current def (missing defs get empty modifiers) and does not re-emit `STATUS_APPLIED`.
