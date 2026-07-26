# Loading Plugin (context.md)

<!-- LLM:OVERVIEW -->

Full-screen loading overlay plus an honest boot gate. On setup it engages physics-hold enforcement (`setLoadingEnforcement(state, true)`) and registers an `assets` ready gate via `gltfAssetsReady` — waits for **critical** GLTF loads (lod0 / hero / props) and every `GltfPending` kick, but **not** background lod1/lod2 streams — plus a `shaders` gate that stays closed until `warmupSceneShaders` finishes silent yaw/pitch compiles (with DistanceCull temporarily forced visible). Other plugins register their own domain gates (terrain decode + collision, spawn placement). Spawn defers on ground mutations (pads/roads/rivers) so trees and enemies share one AABB placement path before the overlay fades. While enforcement is on and the world has not yet been fully ready once, the core `isPhysicsHeld` returns true and the simulation is held, so nothing falls or moves before terrain colliders, assets, and first-look shaders are in place. The overlay itself is a singleton DOM element painted as early as possible (call `mountLoadingScreen()` before building the runtime), driven every frame by `LoadingScreenSystem`, fed by `getLoadingProgress` / `isWorldReady` from `core/loading-gate`. It fades out once the world is ready and a minimum visible time has passed. Opt-in: register with `withPlugin(LoadingPlugin)`.

<!-- /LLM:OVERVIEW -->

## Layout

```
loading/
├── context.md   # This file
├── index.ts     # Public re-exports
├── plugin.ts    # LoadingPlugin (system only)
├── systems.ts   # LoadingScreenSystem (setup + per-frame update)
└── context.ts   # DOM overlay: mount/update/teardown, text, progress bar
```

## Scope

- **In-scope**: Mounting and updating the loading overlay, engaging the core loading gate + `assets` gate, fading out on readiness, teardown safety.
- **Out-of-scope**: The gate registry itself (lives in `core/loading-gate.ts` so physics and gate-providing plugins depend only on core), terrain/spawn gate registration (their own plugins), asset loading mechanics (`extras/gltf-bridge`).

## Entry Points

- **plugin.ts**: `LoadingPlugin` (registers `LoadingScreenSystem`).
- **systems.ts**: `LoadingScreenSystem`.
- **context.ts**: `mountLoadingScreen`, `updateLoadingScreen`, `setLoadingScreenText`, `cancelLoadingFade`.
- **index.ts**: Re-exports.

## Dependencies

- **Internal**: core `registerReadyGate`, `setLoadingEnforcement`, `getLoadingProgress`, `isWorldReady`; `gltf-xml` `gltfAssetsReady` (critical GLTF loads + pending kicks).
- **External**: DOM (`document`, `performance`). No-op in headless mode (`state.headless` or no `document`).

## Integration with the loading gate

The gate registry (`core/loading-gate.ts`) is inert unless a loading screen enables enforcement. `LoadingScreenSystem.setup` does four things:

1. `setLoadingEnforcement(state, true)`: turns on the physics hold. The runtime checks `isPhysicsHeld(state)` (enforcement on AND world not yet latched-ready) and skips the `fixed` / gameplay ticks while it is true. Readiness latches permanently the first time it passes, so transient un-readiness later (e.g. distant terrain chunks rebuilding colliders) never re-triggers the hold.
2. `registerReadyGate(state, 'assets', () => gltfAssetsReady(state))`: critical GLTF gate (lod0 / scene props). Background lod1/lod2 do not block. Terrain and spawn plugins add their own named gates (`terrain`, `spawn`).
3. `registerReadyGate(state, 'shaders', () => isSceneShadersWarmed(state))`: blocks physics latch + fade until the shaders latch opens. Warmup itself is owned by `ShaderWarmupSystem` (rendering plugin) — the overlay driver stops the moment it fades, so it must not own the compile/orbit pump.
4. `mountLoadingScreen()`: paints the overlay (idempotent; also re-mounted on first update as a fallback).

**Ground Y is not a loading gate.** Trees and enemies share the spawner path (`sampleTerrainSurface` + AABB + `TerrainSpawned` + `resyncTerrainSpawnedHeights`). Spawn already defers on `isGroundMutationPending` (pads/roads/rivers). Do **not** add a `settle` gate or keep distant MonoBehaviour scripts awake only to snap feet — see [`../spawner/context.md`](../spawner/context.md) (_Path único de chão_).

`isWorldReady(state)` is true when every registered gate passes (vacuously true with none). `getLoadingProgress(state)` returns `{ ready, total, pending }` which the bar and status line consume.

<!-- LLM:REFERENCE -->

### Component

None. The overlay is a module-scoped singleton in `context.ts` (one per page), kept outside any `State` so it can mount before a runtime exists.

### System

#### LoadingScreenSystem

- Group: `draw`.
- `setup`: if not headless, engages enforcement, registers the `assets` gate, mounts the overlay.
- `update`: if not headless, calls `updateLoadingScreen(state)`.

### Overlay (context.ts)

- `mountLoadingScreen(opts?)`: creates the `#vibegame-loading` fixed overlay (title, subtitle, progress bar, status line) if absent; applies `setLoadingScreenText` live. Call this as the first line of bootstrap for the earliest paint.
- `updateLoadingScreen(state)`: per-frame driver. Reads `getLoadingProgress`; sets `bar.style.width` to `ready/total`; sets status to a humanized pending list (`terrain` -> "Building terrain", `spawn` -> "Placing world objects", `assets` -> "Loading assets", `shaders` -> "Compiling shaders") or "Ready". Fades out (opacity transition) once `isWorldReady` is true AND at least `MIN_VISIBLE_MS` (350ms) elapsed since first show; after `FADE_MS` (450ms) the node is removed.
- `setLoadingScreenText({ title?, subtitle? })` / `getLoadingScreenText()`: copy control.
- `cancelLoadingFade()`: clears the pending fade `setTimeout` and removes the overlay. Call from `runtime.destroy()` so the deferred callback never fires on a detached node.

### Recipe

None.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Earliest possible paint, before the runtime exists (`simple-rpg/src/main.ts`):

```ts
import { mountLoadingScreen, LoadingPlugin, withPlugin } from 'vibegame';

mountLoadingScreen({
  title: 'Crystal Vale',
  subtitle: 'Preparing the world...',
});

withPlugin(LoadingPlugin);
// ...other plugins...
await run();
```

The overlay shows immediately, the bar fills as the `terrain`, `spawn`, `assets`, and `shaders` gates clear, physics is held until all pass, then the screen fades out and gameplay begins.

<!-- /LLM:EXAMPLES -->
