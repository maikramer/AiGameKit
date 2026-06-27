# RPG-Pause Plugin (context.md)

<!-- LLM:OVERVIEW -->

Modal-stack pause coordinator. Any game system that opens a modal UI calls `pushModal(state, name)`; the coordinator freezes the simulation while at least one modal is on the stack and restores it once the stack drains. The freeze is implemented two ways: it sets `state.time.timeScale` to `0` (so `state.time.deltaTime` collapses and gameplay systems that scale by it stall), and it calls `setInputMovementSuppressed(true)` from the `input` plugin so the player controller stops reading movement. When the stack empties, the saved `timeScale` and `inputSuppressed` flag are restored. The coordinator does NOT touch the core `registerReadyGate` / loading gate (that is owned by the `loading` plugin); it is a runtime pause, not a boot gate. State change is broadcast through the `rpg-core` event bus (`PAUSE_PUSHED`, `PAUSE_POPPED`, `PAUSE_CHANGED`). Opt-in: register with `withPlugin(PauseCoordinatorPlugin)`.

<!-- /LLM:OVERVIEW -->

## Layout

```
rpg-pause/
├── context.md   # This file
├── index.ts     # Public re-exports
├── plugin.ts    # PauseCoordinatorPlugin (system + recipe)
└── systems.ts   # PauseState, modal stack API, PauseSystem
```

## Scope

- **In-scope**: Modal-stack pause coordination, time-scale freeze, input movement suppression, change events.
- **Out-of-scope**: Rendering or drawing the modal UI itself (that lives in `hud` / game code), the boot loading gate (`loading` plugin), saving the game on pause (`save-load` plugin).

## Entry Points

- **plugin.ts**: `PauseCoordinatorPlugin` definition (system + `PauseCoordinator` recipe).
- **systems.ts**: `PauseSystem` plus the `pushModal` / `popModal` / `isPaused` API and `PauseState`.
- **index.ts**: Re-exports.

## Dependencies

- **Internal**: core `State` / `System`, `rpg-core` `emitEvent`, `input` `setInputMovementSuppressed`.
- **External**: None.

## How the freeze works

Pause state is kept in a `WeakMap<State, PauseState>` (one per runtime), not in an ECS component, because the coordinator is a singleton per world rather than per entity. `sync()` derives `shouldPause = modalStack.length > 0` and applies two effects every time the stack changes (and again each frame from `PauseSystem`):

1. `state.time.timeScale = shouldPause ? 0 : ps.timeScale`: zeroes the global time scale so every `simulation` system that multiplies by `state.time.deltaTime` stalls.
2. `setInputMovementSuppressed(shouldPause ? true : ps.inputSuppressed)`: flips the input plugin movement flag.

`setTimeScale(state, scale)` and `suppressInput(state, on)` let a game configure slow-motion or input gating that takes effect only while unpaused (the pause override always wins while a modal is open).

<!-- LLM:REFERENCE -->

### Component

None. Pause state lives in the module-scoped `WeakMap`, exposed via `getPauseState(state)`.

### System

#### PauseSystem

- Group: `late` (runs after `simulation` so gameplay reads the post-pause time scale next frame).
- `update`: calls `sync(state, true)`, which recomputes `shouldPause` and emits `PAUSE_CHANGED` when it flips.

### Recipe

- **PauseCoordinator**: `components: []`. A no-op marker recipe; the coordinator is stateless at the ECS level. It exists so games can declare intent declaratively, but the real API is the imperative `pushModal` / `popModal` calls.

### Modal stack API (systems.ts)

- `pushModal(state, name)`: push a named modal onto the stack, emit `PAUSE_PUSHED`, apply effects.
- `popModal(state, name?)`: pop the top (or a specific name via `lastIndexOf`), emit `PAUSE_POPPED`, apply effects. No-op on an empty stack or unknown name.
- `isPaused(state)`: `true` while the stack is non-empty.
- `getActiveModal(state)`: top-of-stack name, or `undefined`.
- `setTimeScale(state, scale)` / `suppressInput(state, on)`: set the unpaused baseline.

### Events (via `rpg-core` `emitEvent`)

- `PAUSE_PUSHED` `{ modal, stack }`, `PAUSE_POPPED` `{ modal, stack }`, `PAUSE_CHANGED` (no payload).

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

Game code (from `simple-rpg/src/game/pause.ts`) opens the shop as a pausing modal:

```ts
import { pushModal, popModal, isPaused } from 'vibegame';

setShopOpen(true);   // internally: pushModal(state, 'shop')  -> sim freezes
setShopOpen(false);  // popModal(state, 'shop')               -> sim resumes
```

The engine pause menu (a `hud` `<TabbedModal>`) pushes its own modal name, so `isPaused(state)` is true for both the shop and the pause menu without each caller needing to know about the other.

<!-- /LLM:EXAMPLES -->
