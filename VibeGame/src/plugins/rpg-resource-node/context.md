# RPG-Resource-Node Plugin (context.md)

<!-- LLM:OVERVIEW -->

Harvestable resource nodes (wood, stone, ore, and any custom kind). The `ResourceNode` component carries harvest state only (kind, yield, respawn cooldown, depleted flag, respawn timestamp); it composes with `Transform`, a visual `GLTFLoader`, and usually `Destructible` rather than owning any of those. The recipe `<ResourceNode kind="..." yield="..." respawn="...">` maps XML to the component, with `kind` resolved through a config enum (`wood=0, stone=1, ore=2` by default, extensible). The imperative `harvest(state, eid)` function grants the yield, emits `NODE_HARVESTED`, and either marks the node depleted with a respawn timer (`respawn > 0`) or leaves it for the caller to remove (one-shot, `respawn == 0`). `ResourceNodeRespawnSystem` restores depleted nodes once their timer elapses and emits `NODE_RESPAWNED`. Opt-in: register with `withPlugin(ResourceNodePlugin)`.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-resource-node/
├── context.md     # This file
├── index.ts       # Public re-exports
├── plugin.ts      # ResourceNodePlugin (component + recipe + system + config)
├── components.ts  # ResourceNode SOA component
├── recipes.ts     # resourceNodeRecipe (ResourceNode tag, parserAttributes: kind)
├── systems.ts     # ResourceNodeRespawnSystem
└── utils.ts       # harvest(), kind enum helpers, event constants + payloads
```

## Scope

- **In-scope**: Resource node data, kind enum resolution, harvest API, respawn timer, harvest/respawn events.
- **Out-of-scope**: The visual mesh (use `GLTFLoader`), destruction FX (use `Destructible` / particles), inventory crediting (the game listens for `NODE_HARVESTED` and credits its inventory/economy plugins).

## Entry Points

- **plugin.ts**: `ResourceNodePlugin` (registers component `resource-node`, recipe, system, defaults, kind enum, and the `ResourceNode` tag parser).
- **utils.ts**: `harvest(state, eid)`, `isDepleted`, `getResourceNodeKind`, kind enum helpers, `NODE_HARVESTED` / `NODE_RESPAWNED`.
- **systems.ts**: `ResourceNodeRespawnSystem`.
- **index.ts**: Re-exports.

## Dependencies

- **Internal**: core `State` / `System` / `defineQuery` / `EnumMapping`, `rpg-core` `emitEvent`.
- **External**: None.

<!-- LLM:REFERENCE -->

### Component

#### ResourceNode (`resource-node`)

- `kind`: ui8 (resource kind enum value; see `config.enums['resource-node'].kind`; default `wood=0, stone=1, ore=2`).
- `yield`: ui16 (amount granted by one harvest).
- `respawn`: ui16 (respawn cooldown in seconds; `0` = one-shot, no respawn, caller removes the entity).
- `depleted`: ui8 (`0` available, `1` depleted, waiting on `respawnAt`).
- `respawnAt`: f64 (`state.time.elapsed` timestamp at which the node becomes available again).

### System

#### ResourceNodeRespawnSystem

- Group: `simulation` (ticks every frame alongside gameplay).
- Query: all entities with `ResourceNode`.
- For each depleted node whose `respawnAt` has elapsed (`now >= respawnAt`, `respawnAt > 0`): clears `depleted` and `respawnAt`, emits `NODE_RESPAWNED` `{ target, kind }`.

### Recipe

- **ResourceNode**: components: `['resource-node']`; `parserAttributes: ['kind']`. The `kind` attribute is parsed by the plugin's `ResourceNode` tag parser (in `config.parsers`), which calls `resolveResourceNodeKind` so string kinds like `"stone"` map to their enum value instead of being coerced to `0` by `Number()`. Other fields (`yield`, `respawn`) flow through the default numeric adapter.

### Config (plugin.ts)

- `defaults['resource-node']`: `kind 0, yield 1, respawn 0, depleted 0, respawnAt 0`.
- `enums['resource-node'].kind`: `{ wood: 0, stone: 1, ore: 2 }`. Extend by registering more entries to add kinds (e.g. `crystal: 3`).
- `parsers.ResourceNode`: reads `element.attributes.kind`, resolves it via `resolveResourceNodeKind`.

### Harvest API (utils.ts)

- `harvest(state, eid)`: returns the yield amount. Emits `NODE_HARVESTED` `{ target, kind, yield, depleted }`. If `respawn > 0`, sets `depleted = 1` and `respawnAt = elapsed + respawn`; if `respawn == 0`, leaves the node untouched (caller removes it). Harvesting an already-depleted node returns `0` and emits nothing.
- `resolveResourceNodeKind(state, value)`: numeric strings pass through; named kinds resolve via the config enum (case-insensitive); unknown kinds fall back to `0`.
- `kindToString(state, kindValue)`: reverse lookup for event payloads.
- `isResourceNode`, `isDepleted`, `getResourceNodeKind`: read helpers.

### Events (via `rpg-core` `emitEvent`)

- `NODE_HARVESTED` (`NodeHarvestedPayload`: `target, kind, yield, depleted`).
- `NODE_RESPAWNED` (`NodeRespawnedPayload`: `target, kind`).

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

In `simple-rpg`, resource nodes are composed inside a `<GameObject>` with a visual `GLTFLoader` and a `Destructible`, placed by a `<StaticSpawner>`:

```xml
<GameObject role="static" script="tree.ts"
            destructible="popup-text: Wood; preset: explosion; hits: 3">
  <GLTFLoader role="visual" url="/assets/meshes/tree_oak_lod0.glb"
              lod1-url="/assets/meshes/tree_oak_lod1.glb"
              lod2-url="/assets/meshes/tree_oak_lod2.glb"></GLTFLoader>
  <ResourceNode kind="wood" yield="3" transform="pos: 0 0 0"></ResourceNode>
</GameObject>
```

Rocks use `kind="stone" yield="3"`. The player chops/mines with **J**; game code listens for `NODE_HARVESTED`, credits the inventory, and the engine respawn system brings the node back after its cooldown (or the one-shot tree is removed once destroyed).

<!-- /LLM:EXAMPLES -->
