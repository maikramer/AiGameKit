# GLTF-Anim Plugin (context.md)

<!-- LLM:OVERVIEW -->

ECS bridge for `GltfAnimator` instances. The plugin owns no recipes of its own; it exposes a **module-global animator registry** plus a `draw`-group system that ticks every registered `GltfAnimator` each frame and syncs its root Three.js `Object3D` pose back to the ECS `WorldTransform` (and flags `Transform` dirty). Owners of animated entities — currently the `player` plugin (`<PlayerGLTF>`) — create a `GltfAnimator`, call `registerAnimator` to get a numeric handle, and store that handle on `GltfAnimationState.registryIndex`. The plugin takes over per-frame ticking and teardown from there.

This is the animation counterpart to `gltf-xml` (static GLB loading). For static props use `<GLTFLoader>`; for an animated player character use `<PlayerGLTF>`, which internally drives this plugin. Programmatic consumers (e.g. `loadGltfToSceneWithAnimator`) can `registerAnimator` directly.

<!-- /LLM:OVERVIEW -->

## Layout

```
gltf-anim/
├── context.md     # This file
├── index.ts       # Public re-exports
├── plugin.ts      # GltfAnimPlugin (system + component + defaults)
├── components.ts  # GltfAnimationState component (SOA typed arrays)
└── systems.ts     # GltfAnimationUpdateSystem, animatorRegistry, register/unregister
```

## Scope

- **In-scope**: ECS animator registry, per-frame mixer ticking, root-pose → `WorldTransform` sync, State-teardown disposal.
- **Out-of-scope**: Model loading (use `gltf-xml` / GLTF bridge), locomotion/clip selection (owned by the `player` plugin via `GltfAnimator` API), rendering, physics.

## Entry Points

- **plugin.ts** — `GltfAnimPlugin` (system + component + defaults).
- **systems.ts** — `GltfAnimationUpdateSystem`, `animatorRegistry`, `registerAnimator`, `unregisterAnimator`.
- **index.ts** — public re-exports (`registerAnimator`, `animatorRegistry`, `GltfAnimationUpdateSystem`, `GltfAnimationState`, `GltfAnimPlugin`).

## Dependencies

- **Internal**: Core ECS (`State`, `System`, `defineQuery`), `GltfAnimator` (`extras/gltf-animator`), `Transform` / `WorldTransform` + `syncEulerFromQuaternion` (`transforms`).
- **External**: Three.js (transitively, via `GltfAnimator`'s `AnimationMixer`).

<!-- LLM:REFERENCE -->

## Public API

### Component: `GltfAnimationState`

Stored as SOA typed arrays (one slot per entity). Keyed into via `registryIndex`.

| Field               | Type   | Default | Meaning                                                                                                   |
| ------------------- | ------ | ------- | --------------------------------------------------------------------------------------------------------- |
| `registryIndex`     | `ui32` | `0`     | Handle into `animatorRegistry`. `0` = no animator (sentinel). `1+` = live handle from `registerAnimator`. |
| `activeClipIndex`   | `ui8`  | `0`     | Reserved/metadata slot (clip selection is owned by `GltfAnimator` callers).                               |
| `isPlaying`         | `ui8`  | `0`     | Reserved/metadata slot.                                                                                   |
| `crossfadeDuration` | `f32`  | `0.25`  | Default crossfade seconds applied when the owner constructs the `GltfAnimator`.                           |

### Registry functions

```ts
registerAnimator(animator: GltfAnimator): number   // add → returns handle (1+)
unregisterAnimator(idx: number): void              // drop + dispose one animator
animatorRegistry: Map<number, GltfAnimator>        // module-global handle → animator
```

`registerAnimator` assigns a monotonically increasing handle (starting at 1, so `0` stays the "no animator" sentinel) and stores the animator. `unregisterAnimator` looks the handle up, calls `animator.dispose()`, and removes the entry; it is a no-op for unknown handles.

### System: `GltfAnimationUpdateSystem`

- **Group**: `draw` (ticks after simulation/physics, before render).
- **Per entity with `GltfAnimationState` where `registryIndex !== 0`**:
  1. Looks up the `GltfAnimator` via `animatorRegistry.get(registryIndex)`. Skips if missing (orphan guard — e.g. entity destroyed mid-frame, or handle stale).
  2. Calls `animator.update(deltaTime)` to advance the `AnimationMixer` (crossfades, additive overlays, clip time).
  3. If the entity has `WorldTransform`, copies the animator **root** `Object3D` position/quaternion into `WorldTransform`, re-derives Euler angles, and — when `Transform` is also present — sets `Transform.dirty = 1` so downstream hierarchy systems recompute.

### Plugin: `GltfAnimPlugin`

Registers `GltfAnimationUpdateSystem`, the `GltfAnimationState` component, and component defaults (`registryIndex=0`, `activeClipIndex=0`, `isPlaying=0`, `crossfadeDuration=0.25`). Registered in `DefaultPlugins`.

## Lifecycle & disposal

Teardown is layered so animators are released even when entities die mid-frame or a whole `State` is destroyed:

1. **Per-entity (owner-driven)** — the owner of an animated entity registers an `onDestroy(eid, …)` that calls `unregisterAnimator(handle)`. The `player` plugin (`PlayerGltfSetupSystem`) does this; it also guards async loads with `state.exists(eid)` so a model that finishes loading after its entity was destroyed never leaks an orphan animator into the registry.
2. **Per-frame orphan guard** — `update` skips entries whose handle is `0` or whose animator is gone from the map, so a mid-frame destroy cannot dereference a stale animator.
3. **State teardown (system `dispose`)** — `GltfAnimationUpdateSystem.dispose(state)` is the safety net when a `State` is torn down. It iterates **every** remaining animator in `animatorRegistry`, best-effort calls `animator.dispose()` on each (wrapped in try/catch so one already-disposed animator can't abort the sweep), clears the map, and resets the handle counter to `1`. This mirrors the canonical dispose pattern used by `bvh` (`disposeBvhContext`) and `gltf-xml` (cache clear on teardown).

> **Registry scope.** `animatorRegistry` is a module-global `Map`, not a `WeakMap<State, …>` like `bvh`'s context. This is intentional: the public `registerAnimator(animator)` signature takes no `state`, so per-State keying would require an API break. Full clear on `dispose(state)` keeps teardown correct for the engine's single-active-State lifecycle; per-entity `onDestroy` handles the entity-granularity case.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

Register an animator from a loaded GLTF and let the plugin tick it:

```ts
import { GltfAnimator, registerAnimator } from 'vibegame';
import { loadGltfAnimated, run } from 'vibegame';

const state = await run();
const gltf = await loadGltfAnimated(state, '/assets/models/hero.glb');

const animator = new GltfAnimator(gltf, { crossfadeDuration: 0.25 });
const handle = registerAnimator(animator);

// Store the handle on the entity so GltfAnimationUpdateSystem ticks it:
//   GltfAnimationState.registryIndex[eid] = handle;
animator.play('idle');
```

In XML, `<PlayerGLTF>` wires all of this up automatically — you do not call `registerAnimator` yourself for the player character.

```html
<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
```

<!-- /LLM:EXAMPLES -->

## Known limitations

- **Single global registry.** One `animatorRegistry` per process; not isolated per `State`. Correct for the engine's single-active-State model; a multi-State host would need to dispose the prior State before booting a new one (the `dispose(state)` hook handles that).
- **No declarative recipe.** The plugin registers no XML tag; animated entities are created programmatically (player plugin, GLTF bridge) and opt into ticking by writing `GltfAnimationState.registryIndex`.
- **Clip selection is the owner's job.** `GltfAnimationState.activeClipIndex` / `isPlaying` are metadata slots; the actual locomotion / override logic lives on `GltfAnimator` and is driven by the owning system (e.g. `PlayerGltfAnimStateSystem`).
- **Root-pose sync is one-way.** The system copies `animator.root` → `WorldTransform`; it does not write ECS transform values back onto the animator root. Physics-driven motion should move the ECS `Transform` and let the player controller reseat the GLTF root, not the other way round.
