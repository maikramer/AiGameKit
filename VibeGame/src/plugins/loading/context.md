# Loading Plugin (context.md)

<!-- LLM:OVERVIEW -->

Full-screen loading overlay plus an honest boot gate. On setup it engages physics-hold enforcement (`setLoadingEnforcement(state, true)`) and registers a generic `assets` ready gate that waits for all in-flight GLTF loads to finish. Other plugins register their own domain gates (terrain decode + collision, spawn placement). While enforcement is on and the world has not yet been fully ready once, the core `isPhysicsHeld` returns true and the simulation is held, so nothing falls or moves before terrain colliders and assets are in place. The overlay itself is a singleton DOM element painted as early as possible (call `mountLoadingScreen()` before building the runtime), driven every frame by `LoadingScreenSystem`, fed by `getLoadingProgress` / `isWorldReady` from `core/loading-gate`. It fades out once the world is ready and a minimum visible time has passed. Opt-in: register with `withPlugin(LoadingPlugin)`.

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

- **Internal**: core `registerReadyGate`, `setLoadingEnforcement`, `getLoadingProgress`, `isWorldReady`; `extras/gltf-bridge` `getActiveGltfLoadCount`.
- **External**: DOM (`document`, `performance`). No-op in headless mode (`state.headless` or no `document`).

## Integration with the loading gate

The gate registry (`core/loading-gate.ts`) is inert unless a loading screen enables enforcement. `LoadingScreenSystem.setup` does three things:

1. `setLoadingEnforcement(state, true)`: turns on the physics hold. The runtime checks `isPhysicsHeld(state)` (enforcement on AND world not yet latched-ready) and skips the `fixed` / gameplay ticks while it is true. Readiness latches permanently the first time it passes, so transient un-readiness later (e.g. distant terrain chunks rebuilding colliders) never re-triggers the hold.
2. `registerReadyGate(state, 'assets', () => getActiveGltfLoadCount() === 0)`: the generic GLTF gate. Terrain and spawn plugins add their own named gates (`terrain`, `spawn`).
3. `mountLoadingScreen()`: paints the overlay (idempotent; also re-mounted on first update as a fallback).

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
- `updateLoadingScreen(state)`: per-frame driver. Reads `getLoadingProgress`; sets `bar.style.width` to `ready/total`; sets status to a humanized pending list (`terrain` -> "Building terrain", `spawn` -> "Placing world objects", `assets` -> "Loading assets") or "Ready". Fades out (opacity transition) once `isWorldReady` is true AND at least `MIN_VISIBLE_MS` (350ms) elapsed since first show; after `FADE_MS` (450ms) the node is removed.
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

The overlay shows immediately, the bar fills as the `terrain`, `spawn`, and `assets` gates clear, physics is held until all pass, then the screen fades out and gameplay begins.

<!-- /LLM:EXAMPLES -->
