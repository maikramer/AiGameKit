# Debug Plugin (context.md)

<!-- LLM:OVERVIEW -->

Opt-in debugging plugin with two independent surfaces. (1) **PostFxDebugToggle**, a legacy declarative toggle that cycles post-processing effect fields (bloom, chromaticAberration, vignette, aa, ssao, toneMapping) via configurable key bindings and disposes the active post-processing pass so it rebuilds. (2) **The registry API** (`registry.ts`), a DEV-only `registerDebugAction` / `registerDebugVar` registry that other plugins and app code populate with callable actions and inspectable variables; in production builds both registrars are no-ops so nothing ships. The plugin also installs `window.__VIBEGAME__`, a read-only introspection bridge over the live ECS `State` for Playwright or console QA, exposes a `.debug` namespace on that bridge for invoking the registry, and renders a fixed-position stats overlay (FPS, frame time, entity/system/coroutine counts, GLTF loads in flight) toggled with `?` and wireframe toggled with `*`. Not in `DefaultPlugins`; register it explicitly so it never reaches production.

<!-- /LLM:OVERVIEW -->

## Layout

```
debug/
├── context.md       # This file
├── index.ts         # Re-exports PostFx toggle API + registry API + types + bridge type
├── plugin.ts        # DebugPlugin, DebugOverlaySystem, createBridge, __VIBEGAME__ install
├── postfx-toggle.ts # PostFxToggleSystem, postFxToggleRecipe, applyPostFxToggle, bindings parser
└── registry.ts      # registerDebugAction / registerDebugVar / getDebugRegistry(Handle)
```

## Scope

- **In-scope**: Post-FX keyboard toggles, in-browser stats overlay, wireframe toggle, ECS introspection bridge, DEV-only debug action/var registry, registry rendering inside the overlay.
- **Out-of-scope**: Performance tracing (use the browser profiler), scene graph editing UI, production telemetry. Everything here is a dev affordance.

## Entry Points

- **plugin.ts**: `DebugPlugin` (systems `PostFxToggleSystem`, `DebugOverlaySystem`; recipe `PostFxDebugToggle`; `initialize` installs `window.__VIBEGAME__`).
- **postfx-toggle.ts**: `PostFxToggleSystem`, `postFxToggleRecipe`, `applyPostFxToggle`, `parsePostFxBindings`, `setPostFxBindings`, `getPostFxToggleState`, `DEFAULT_POSTFX_BINDINGS`.
- **registry.ts**: `registerDebugAction`, `registerDebugVar`, `getDebugRegistry`, `getDebugRegistryHandle`.
- **index.ts**: Re-exports all of the above plus types (`VibeGameDebugBridge`, `DebugRegistry`, `DebugRegistryHandle`, `DebugActionEntry`, `DebugVarEntry`, `PostFxEffectField`, `PostFxKeyBindings`, ...).

## Dependencies

- **Internal**: Core ECS, `input/utils` (`isKeyDown`), `rendering/utils` (`getRenderingContext`, `getScene`), `postprocessing/components` (`Postprocessing`), `physics/systems` (`getPhysicsContext`), `terrain/utils` (`getTerrainContext`), `extras/gltf-bridge` (`getActiveGltfLoadCount`).
- **External**: None beyond what the imported plugins already pull in. Vite's `import.meta.env.DEV` gates the registry.
<!-- LLM:REFERENCE -->

### Components

None declared by this plugin. The PostFx toggle reads and mutates the `Postprocessing` component owned by the postprocessing plugin.

### Systems (order in the plugin)

1. **PostFxToggleSystem** (`simulation`) - queries `Postprocessing` entities, takes the first one, and for each binding checks `isKeyDown`. On a fresh keypress (debounced per code) it increments the effect field modulo its cycle length and pushes the changed field. If anything toggled, it disposes `ctx.postProcessing` and sets it to `undefined` so the rendering pass rebuilds with the new mix.
2. **DebugOverlaySystem** (`draw`, `last: true`) - skips when `state.headless`. Ensures the overlay div (`#vibegame-debug-overlay`) exists, updates an EWMA FPS plus a 60-frame ring buffer of frame times, and every 10 frames rewrites the text content: FPS, average/min/max frame ms, entity count, system count, active coroutine count, GLTF loads in flight, the `?` / `*` hint line, and the registry section (Vars then Actions) when the overlay is visible. `dispose` removes the key listener and the DOM node.

### Recipe

- **`<PostFxDebugToggle bindings="...">`** - a parser-only recipe (no components). The plugin's `parsers.PostFxDebugToggle` reads the `bindings` attribute and calls `setPostFxBindings(state, parsePostFxBindings(raw))`. With no attribute the defaults (`Digit1`..`Digit6`) remain in effect.

### PostFx toggle details (`postfx-toggle.ts`)

Effect fields and their cycle modulus (how many keypresses before the value wraps to 0):

| Field                 | Alias strings               | Modulus |
| --------------------- | --------------------------- | ------- |
| `bloom`               | `bloom`                     | 2       |
| `chromaticAberration` | `ca`, `chromaticaberration` | 2       |
| `vignette`            | `vignette`                  | 2       |
| `aa`                  | `aa`                        | 3       |
| `toneMapping`         | `tonemapping`               | 5       |
| `ssao`                | `ssao`                      | 2       |

Default bindings: `Digit1` bloom, `Digit2` chromaticAberration, `Digit3` vignette, `Digit4` aa, `Digit5` ssao, `Digit6` toneMapping. Binding string format is `Keycode:effect,Keycode:effect` (case-insensitive effect, lowercase aliases above), parsed by `parsePostFxBindings`.

### Registry API (`registry.ts`, new in E4)

A per-state registry held in a `WeakMap<State, DebugRegistry>` where `DebugRegistry = { actions: Map<string, DebugActionEntry>, vars: Map<string, DebugVarEntry> }`. Public functions:

- **`registerDebugAction<T>(state, name, fn, { description? })`** - stores a callable. No-op when `import.meta.env.DEV === false`.
- **`registerDebugVar(state, name, getter, setter?)`** - stores an inspector (optionally mutable). No-op when `import.meta.env.DEV === false`.
- **`getDebugRegistry(state)`** - returns the raw registry (maps of entries).
- **`getDebugRegistryHandle(state)`** - returns a `DebugRegistryHandle` facade with `actionNames()`, `varNames()`, `hasAction(name)`, `hasVar(name)`, `callAction(name, ...args)`, `getVar(name)`, `setVar(name, value)` (returns false if the var is read-only or unknown).

Because both registrars short-circuit in production, calling them from inside other plugins is safe and free in release builds.

### `__VIBEGAME__` bridge (plugin.ts)

`DebugPlugin.initialize` sets `window.__VIBEGAME__` exactly once (it bails if already present) to a `VibeGameDebugBridge` built by `createBridge(state)`. Members:

| Member                 | Returns                                                           |
| ---------------------- | ----------------------------------------------------------------- |
| `state`                | the live `State`                                                  |
| `snapshot(options?)`   | formatted world snapshot string                                   |
| `entities()`           | array of `{ eid, name, components }`                              |
| `entity(name)`         | `{ eid, name, components }` or null                               |
| `component(eid, name)` | field-to-number map for one component, or null                    |
| `query(...names)`      | eids matching all named components                                |
| `componentNames()`     | all registered component names                                    |
| `namedEntities()`      | `{ name, eid }` pairs                                             |
| `step(dt?)`            | advance the world one tick                                        |
| `terrain()`            | terrain context dump                                              |
| `rendering()`          | rendering context                                                 |
| `physics()`            | physics context                                                   |
| `debug`                | a `DebugRegistryHandle` (same facade as `getDebugRegistryHandle`) |

The `.debug` namespace is the canonical way for Playwright or the browser console to invoke registered actions (`__VIBEGAME__.debug.callAction('respawn-player')`) or read registered vars (`__VIBEGAME__.debug.getVar('player.hp')`).

### Overlay keyboard shortcuts

Active only when the plugin is registered and the page is not headless, and ignored while focus is in a text input, `contentEditable`, `INPUT`, or `TEXTAREA`:

| Key           | Action                                                |
| ------------- | ----------------------------------------------------- |
| `?` (Shift+/) | Toggle the stats overlay (hidden by default).         |
| `*` (Shift+8) | Toggle wireframe on every mesh material in the scene. |

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```ts
// Register a dev-only action and var from your own system.
import { registerDebugAction, registerDebugVar } from 'vibegame';

registerDebugAction(state, 'respawn-player', () => spawnPlayer(state), {
  description: 'Teleport the player back to spawn',
});
registerDebugVar(state, 'player.hp', () => Player.hp[playerEid], (v) => {
  Player.hp[playerEid] = Number(v);
});
```

Drive it from the console or Playwright via the bridge:

```js
__VIBEGAME__.debug.varNames();              // ['player.hp']
__VIBEGAME__.debug.getVar('player.hp');     // 100
__VIBEGAME__.debug.setVar('player.hp', 50); // true
__VIBEGAME__.debug.callAction('respawn-player');
__VIBEGAME__.entities();                    // full ECS dump
```

Declare the legacy PostFx toggle in XML with custom bindings:

```html
<PostFxDebugToggle bindings="KeyB:bloom, KeyV:vignette, KeyN:ca"></PostFxDebugToggle>
```

With no `bindings` attribute, the defaults (`Digit1`..`Digit6`) apply. Press `?` in the page to reveal the overlay (including the registered Vars and Actions sections), `*` to flip wireframe on.

<!-- /LLM:EXAMPLES -->

## Known Limitations

- The registry is DEV-only. Any `registerDebugAction` / `registerDebugVar` call compiles to a no-op in production (`import.meta.env.DEV === false`), so never put load-bearing logic inside a registered action.
- `PostFxToggleSystem` operates on the **first** `Postprocessing` entity only; it assumes a single global post-processing volume.
- Toggling an effect disposes the whole `ctx.postProcessing` pass and lets the render loop rebuild it. Expect a one-frame hitch on each toggle.
- `__VIBEGAME__` is installed once and never replaced, even if `DebugPlugin.initialize` runs again (for example after a hot reload). Restart the page to pick up a fresh bridge.
- The overlay is a fixed `pointer-events: none` div; it cannot be clicked. Use the keyboard shortcuts or the `__VIBEGAME__` bridge instead.
- `setVar` returns `false` silently for unknown or read-only vars; there is no error event.
