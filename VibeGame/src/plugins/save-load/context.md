# Save-Load Plugin (context.md)

<!-- LLM:OVERVIEW -->

Opt-in persistence plugin. Snapshots live ECS state, serializes flagged entities (and registered component kinds) into a versioned blob, and restores it. Two layers ship together: a low-level **msgpackr snapshot** of the whole `WorldSnapshot` (`serializer.ts`) and a higher-level **serializer registry** (`serializer-registry.ts`) that walks every entity, asks each registered `SaveSerializer` for a JSON-friendly kind payload, and emits a `SaveSnapshot { version: '1.0', entities, globals? }`. `localStorage` helpers wrap the bytes as `JSON.stringify(Array.from(buf))` under a caller-supplied key (there is no default key). The RPG serializers (`rpg-serializers.ts`) register vault, inventory, progression, and status-effect kinds at `initialize()` time, but only when the matching component is already registered, so this plugin stays safe to load without the RPG suite.

<!-- /LLM:OVERVIEW -->

## Layout

```
save-load/
├── context.md             # This file
├── index.ts               # Public re-exports
├── plugin.ts              # SaveLoadPlugin (system + component + registers RPG serializers)
├── components.ts          # Serializable (flag, serializationId)
├── systems.ts             # SerializationIdSystem (group: setup)
├── serializer.ts          # msgpackr snapshot + localStorage helpers
├── serializer-registry.ts # registerSaveSerializer / serializeAll / deserializeAll / transient exclusions
└── rpg-serializers.ts     # vault / inventory / progression / status-effect serializers
```

## Scope

- **In-scope**: World snapshot to msgpackr bytes, localStorage save/load, per-kind component serializers, transient entity exclusion, global (non-entity) serializers.
- **Out-of-scope**: Cloud sync, encrypted saves, autosave scheduling, UI for save slots (the app builds those on top of `saveSnapshot` / `loadSnapshot`).

## Entry Points

- **plugin.ts**: `SaveLoadPlugin` (registers `SerializationIdSystem`, the `serializable` component, and calls `registerRpgSaveSerializers` in `initialize`).
- **serializer.ts**: `saveSnapshot`, `loadSnapshot`, `saveToLocalStorage`, `loadFromLocalStorage`, `assignSerializationIds`.
- **serializer-registry.ts**: `registerSaveSerializer`, `serializeAll`, `deserializeAll`, transient exclusion API.
- **index.ts**: Re-exports everything above plus types.

## Dependencies

- **Internal**: Core ECS (`createSnapshot`, `restoreSnapshot`, `WorldSnapshot`), RPG plugins (optional, weak: `rpg-vault`, `rpg-inventory`, `rpg-progression`, `rpg-status`), `particles/components` (for the particle-burst transient matcher).
- **External**: `msgpackr` (`Packr`), `bitecs` (`commitRemovals`, `getAllEntities`).
<!-- LLM:REFERENCE -->

### Component

#### Serializable

- `flag`: ui8 (1 = this entity opts in to the registry-based save; 0 = ignored by `serializeAll` unless another serializer claims it).
- `serializationId`: ui32 (stable id assigned once by `assignSerializationIds`, starting at 1; 0 means "not yet assigned").

### System

#### SerializationIdSystem

- Group: `setup`.
- Runs `assignSerializationIds(state)` once per setup pass: for every entity with `Serializable.flag === 1` and `serializationId === 0`, assigns the next monotonic id. Ids persist for the entity's lifetime.

### Recipe

None. Entities opt in by getting the `serializable` component (typically via `<GameObject serializable>` or by adding the component in code).

### Snapshot contract (msgpackr layer, `serializer.ts`)

`saveSnapshot(state)` builds `{ ...createSnapshot(state), serializableEids: number[] }` (the eids where `Serializable.flag === 1`) and returns `packr.pack(payload)` as `Uint8Array`. `loadSnapshot(state, data, { clearExisting })` unpacks, optionally destroys every existing serializable entity and calls `commitRemovals`, then `restoreSnapshot(state, payload)`. After restore it dispatches `window.CustomEvent('snapshot-loaded', { detail: payload })` when running in a browser. `saveToLocalStorage` / `loadFromLocalStorage` wrap the bytes as `JSON.stringify(Array.from(buf))` under the given key and return `boolean` for load (false if missing or `localStorage` unavailable).

### Serializer registry (`serializer-registry.ts`)

Two `WeakMap<State, Map<string, ...>>` registries, one for per-entity `SaveSerializer` kinds and one for global `GlobalSaveSerializer` kinds. `serializeAll(state)` walks every entity in the world plus every `Serializable.flag === 1` entity, skips `isTransientEntity`, asks each registered serializer for a kind payload, drops entities with no payload and no serializable flag, attaches the named-entity name when present, sorts by eid, then appends globals. Output shape:

```
SaveSnapshot {
  version: '1.0',
  entities: SerializableEntitySnapshot[] { eid, name?, kinds: Record<kind, unknown> },
  globals?: Record<kind, unknown>        // omitted when empty
}
```

`deserializeAll(state, snapshot)` reuses the existing eid if it still exists, otherwise creates a fresh entity, then calls each kind's `deserialize`. Globals are optional and skipped on older snapshots.

### Registered kinds (RPG, conditional on component presence)

| Kind string     | Source plugin     | Serializer pair                                                     |
| --------------- | ----------------- | ------------------------------------------------------------------- |
| `vault`         | `rpg-vault`       | `getVaultEntitySnapshot` / `applyVaultEntitySnapshot`               |
| `inventory`     | `rpg-inventory`   | `getInventoryEntitySnapshot` / `applyInventoryEntitySnapshot`       |
| `progression`   | `rpg-progression` | `getProgressionEntitySnapshot` / `applyProgressionEntitySnapshot`   |
| `status-effect` | `rpg-status`      | `getStatusEffectEntitySnapshot` / `applyStatusEffectEntitySnapshot` |

Each serializer re-adds its component via `addComponent` only when that component is registered, so a snapshot saved in one app and loaded in another without that RPG plugin degrades gracefully (the kind is read but the component is never reattached).

### Transient exclusions (the "not-serialized" list)

Registered once globally by `registerTransientExclusions()` (idempotent). Any entity matching one of these is dropped by `serializeAll` even if it is flagged serializable:

| Name             | Component matched  | Extra matcher                                                                                          |
| ---------------- | ------------------ | ------------------------------------------------------------------------------------------------------ |
| `projectile`     | `projectile-data`  | none                                                                                                   |
| `floating-text`  | `floating-text`    | none                                                                                                   |
| `particle-burst` | `particle-emitter` | only when `ParticleEmitter.burst[eid] === 1` (continuous emitters are saved; one-shot bursts are not). |

Call `registerTransientExclusion({ name, component, matches? })` to add more from another plugin.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```ts
import { saveToLocalStorage, loadFromLocalStorage, serializeAll, deserializeAll } from 'vibegame';

// Simple: whole-world bytes in localStorage under a fixed key.
saveToLocalStorage(state, 'my-game-save');
const ok = loadFromLocalStorage(state, 'my-game-save');

// Structured: walk the registry and ship JSON to a server.
const snapshot = serializeAll(state);
await fetch('/api/save', { method: 'POST', body: JSON.stringify(snapshot) });

// Restore later (reuses eids when present, creates new ones otherwise).
const snap = await (await fetch('/api/save')).json();
deserializeAll(state, snap);
```

Opt an entity in by giving it the `serializable` component (the setup system assigns it a stable id):

```html
<GameObject name="player" serializable="1"></GameObject>
```

<!-- /LLM:EXAMPLES -->

## Known Limitations

- The msgpackr layer (`saveSnapshot` / `loadSnapshot`) and the registry layer (`serializeAll` / `deserializeAll`) are independent. The registry layer produces plain JSON and is the recommended path for app-driven saves; the msgpackr layer is a lower-level world dump that also embeds `serializableEids`.
- `localStorage` serialization is a JSON array of byte numbers, which roughly quadruples the size of the binary payload. For large worlds prefer `saveSnapshot` bytes stored in IndexedDB.
- `serializationId` is stable only within a running process; it is not persisted across reloads, so do not use it as a portable save key.
- Global serializers are skipped silently on snapshots that predate the `globals` field (backwards compatible).
