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
├── panel.ts     # DOM panel (Summary / Systems / Counters)
└── handle.ts    # __VIBEGAME__.profiler bridge API
```

## Scope

- **In-scope**: Per-system / per-group timings, custom spans (`withSpan` / `beginSpan` / `endSpan`), in-game panel, User Timing (`deep`), JSON snapshot/download/copy, URL `?profiler=1|deep`, renderer.info + terrain/BVH counters.
- **Out-of-scope**: Spector.js WebGL frame capture, remote telemetry, production-on-by-default.

## Entry Points

- **plugin.ts**: `ProfilerPlugin` (system `ProfilerPanelSystem`; `initialize` installs bridge + key listener + URL flag).
- **Core API** (`src/core/profiler`): `enableProfiler`, `withSpan`, `getProfilerSnapshot`, `getProfilerTop`, `namedSystem`.
- **Bridge**: `__VIBEGAME__.profiler.enable()`, `.snapshot()`, `.top(15)`, `.setMode('deep')`, `.download()`, `.copy()`.

## Keyboard / URL

| Input | Action |
|-------|--------|
| `P` | Toggle panel (enables `sample` mode) |
| `Shift+P` | Cycle `sample` ↔ `deep` |
| `Pause` | Freeze / unfreeze snapshot |
| `?profiler=1` | Open on load (`sample`) |
| `?profiler=deep` | Open on load with User Timing marks |

## Dependencies

- Core profiler session, rendering context, adaptive-quality tier, terrain stats, BVH stats, gltf load counter.
