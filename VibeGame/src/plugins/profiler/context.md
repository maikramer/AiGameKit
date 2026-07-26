# Profiler Plugin (context.md)

<!-- LLM:OVERVIEW -->

Opt-in hierarchical frame profiler for finding CPU bottlenecks. Instruments the ECS scheduler (per-system and per-group timings) and the GameRuntime WebGL submit pass (`render`). Provides an interactive in-game panel (filterable system list, group bars, renderer/terrain/BVH counters), Chrome User Timing marks in `deep` mode, and JSON export via `window.__VIBEGAME__.profiler`. Not in `DefaultPlugins` — register explicitly (e.g. alongside `DebugPlugin` in examples).

<!-- /LLM:OVERVIEW -->

## Layout

```
profiler/
├── context.md   # This file
├── index.ts     # Public exports
├── plugin.ts    # ProfilerPlugin, hotkeys, URL bootstrap, panel system
├── panel.ts     # DOM panel (Systems / Audio tabs)
├── url.ts       # ?profiler= / ?profilerTab= parsing
└── handle.ts    # __VIBEGAME__.profiler bridge API
```

## Scope

- **In-scope**: Per-system / per-group timings, custom spans (`withSpan` / `beginSpan` / `endSpan`), in-game panel (**Systems** + **Audio** tabs), User Timing (`deep`), JSON snapshot/download/copy, URL `?profiler=1|deep|audio`, renderer.info + terrain/BVH + GLB instance-pool counters, audio play attribution via `__VIBEGAME__.audio`.
- **Out-of-scope**: Spector.js WebGL frame capture, remote telemetry, production-on-by-default.

## Entry Points

- **plugin.ts**: `ProfilerPlugin` (system `ProfilerPanelSystem`; `initialize` installs bridge + key listener + URL flag).
- **Core API** (`src/core/profiler`): `enableProfiler`, `withSpan`, `getProfilerSnapshot`, `getProfilerTop`, `namedSystem`.
- **Bridge**: `__VIBEGAME__.profiler.enable()`, `.snapshot()`, `.top(15)`, `.setMode('deep')`, `.download()`, `.copy()`.

## Keyboard / URL

| Input                                | Action                                        |
| ------------------------------------ | --------------------------------------------- |
| `P`                                  | Toggle panel (enables `sample` mode)          |
| `Shift+P`                            | Cycle `sample` ↔ `deep`                       |
| `Pause`                              | Freeze / unfreeze snapshot                    |
| `?profiler=1`                        | Open on load (`sample`, Systems tab)          |
| `?profiler=deep`                     | Open on load with User Timing marks           |
| `?profiler=audio`                    | Open **Audio** tab + arm stack capture        |
| `?profiler=world`                    | Open **World** tab (player / camera / nearby) |
| `?profilerTab=systems\|audio\|world` | Select tab (with `profiler=1`)                |
| `?audioDebug=1`                      | Arm audio debug (opens Audio tab if unset)    |

### Audio tab

Reads `getAudioDebugSnapshot()` from `plugins/audio/debug-log.ts`:

- Active plays, buses, ctx state
- Recent log: gameplay `play`/`stop`/…; boot `preload` summarized separately
- `topKeys` / `topOrigins` (who fired — pass `originEid` on `playSound*`)
- Buttons: Clear log (keeps preload summary), Stop all, Mute sfx/music

Bridge: `__VIBEGAME__.audio.snapshot()` / `.clearLog({ keepPreload: true })`.
Full contract: [`docs/AUDIO.md`](../../../docs/AUDIO.md).

### World tab

Reads `getWorldDebugSnapshot(state)` from `plugins/profiler/world-debug.ts`:

- Player: name, eid, pos / worldPos, yaw, grounded, `groundY` (mesh lattice) + `terrainY` (analytic), density boost, velocity
- Camera: MainCamera pose, fov/near/far, Three.js position, ThirdPersonCamera (yaw/pitch/follow)
- Nearby (tab text): label from entity name → GLB stem (self or child `GLTFLoader`) → script → collider mesh → tag — not bare `#eid` when a mesh/script exists; dist + pos + tags
- Nearby JSON (`detail.*`): transform, ground/surface ΔY, density boost, parent/children, gltf/lod URLs, script, health, faction, AI mode, destructible, `TerrainSpawned`, cull, nav, rigidbody, collider, resource, variation, component list

Bridge: `__VIBEGAME__.profiler.worldSnapshot()` / `.setTab('world')` — JSON includes full `detail`.

## Counters (panel)

The Counters tab aggregates live engine stats:

| Line                                       | Source                                                         |
| ------------------------------------------ | -------------------------------------------------------------- |
| `renderer.info`                            | Three.js draw calls / triangles / textures                     |
| `adaptiveQuality.tier`                     | Adaptive quality plugin                                        |
| `entities` / `systems` / `gltfLoads`       | ECS + `getActiveGltfLoadCount`                                 |
| `gltfInstances: pools=… slots=… pending=…` | `getInstancePoolStats(state)` from `gltf-xml/auto-instance.ts` |
| `bvh meshes=…`                             | BVH utils                                                      |
| `terrain[…]: chunks=…`                     | `getTerrainStats`                                              |

`getInstancePoolStats` must stay exported — a named import that is missing breaks the whole panel module at load time (optional chaining on the call does not help).

## Dependencies

- Core profiler session, rendering context, adaptive-quality tier, terrain stats, BVH stats, gltf load counter, `getInstancePoolStats` (gltf-xml auto-instance).
