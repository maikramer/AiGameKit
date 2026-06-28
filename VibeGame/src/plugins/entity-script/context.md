# Entity-Script Plugin (context.md)

<!-- LLM:OVERVIEW -->

Unity-style **MonoBehaviour** scripts attached to entities. Author a `.ts` module
that exports named lifecycle functions (`start`, `update`, ...), then point an
entity at it with the `script="file.ts"` attribute. The plugin loads the module
via a Vite `import.meta.glob`, caches it, and calls the hooks on the right frame
group. A script can be attached either as a `script=` attribute on any recipe
(`<GameObject script="wolf.ts">`) or as a `<MonoBehaviour script="...">` child
of an entity. Both forms land the `monoBehaviour` component on the same entity,
so they are interchangeable. A game registers its script folder once with
`registerEntityScripts(state, import.meta.glob('./scripts/**/*.ts'))`; the glob
key is resolved by basename (`wolf.ts` or `enemies/wolf.ts`). One script module
per entity (see Known Limitations).

<!-- /LLM:OVERVIEW -->

## Layout

```
entity-script/
├── context.md     # This file
├── plugin.ts       # EntityScriptPlugin: recipe + systems + config
├── components.ts   # MonoBehaviour component (ready, enabled)
├── system.ts       # Lifecycle + collision systems
├── context.ts      # Per-state maps: script file, glob, module cache, collision pairs
├── recipes.ts      # MonoBehaviour recipe (merge: true)
├── types.ts        # MonoBehaviourModule, MonoBehaviourContext, CollisionOther
└── index.ts        # Public re-exports
```

## Scope

- **In-scope**: Per-entity TS script modules, full MonoBehaviour lifecycle, collision/trigger bridges, coroutine helpers on the context.
- **Out-of-scope**: Editor hot-reload of script files (Vite HMR applies at dev time), multiple scripts per entity, visual scripting.

## Entry Points

- **plugin.ts**: `EntityScriptPlugin` (in `DefaultPlugins`).
- **system.ts**: `EntityScriptSystem` (simulation), `EntityScriptFixedUpdateSystem` (fixed), `EntityScriptLateUpdateSystem` (late), `EntityScriptCollisionBridgeSystem` (simulation).
- **context.ts**: `registerEntityScripts` and module resolution helpers.

## Dependencies

- **Internal**: Core ECS (State, query, coroutines), `transforms` (Transform), `physics` (Collider, TouchedEvent, TouchEndedEvent), `gltf-xml` (GltfPending wait, `getGltfRootGroup` for `object3d`).
- **External**: Vite `import.meta.glob` for module discovery.

## Wiring a script folder

Scripts resolve through a Vite glob registered once at boot; the `script`
attribute basename is matched against glob keys.

```ts
import * as GAME from 'vibegame';
const state = await GAME.run();
GAME.registerEntityScripts(state, import.meta.glob('./scripts/**/*.ts'));
```

<!-- LLM:REFERENCE -->

## Component

### MonoBehaviour

- `ready`: ui8 (0 = not yet started, 1 = started and ticking).
- `enabled`: ui8 (1 = active, 0 = paused). Defaults to `enabled: 1`, `ready: 0`.

## Lifecycle hooks

All hooks receive a `MonoBehaviourContext` (`ctx`). Collision/trigger hooks also
receive a `CollisionOther` (`{ entity }`). Hooks are optional; export only what
you need. A module must export at least `start` or `update`, otherwise it is
treated as not a MonoBehaviour and a warning is logged.

| Hook                           | System group            | When it fires                                                                                                                                                                              |
| ------------------------------ | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `awake(ctx)`                   | simulation (setup pass) | Once, the first frame the module is loaded and the entity is enabled. Runs before `onEnable` and `start`.                                                                                  |
| `onEnable(ctx)`                | simulation              | When `enabled` flips to 1. Also called once during setup if the entity starts enabled.                                                                                                     |
| `start(ctx)`                   | simulation (setup pass) | Once, after `awake`/`onEnable`. May be async (`Promise<void>`); the setup chain `await`s it. If the entity has a pending GLB (`gltf-pending`), setup waits until the GLB finishes loading. |
| `update(ctx)`                  | simulation              | Every simulation frame while `enabled === 1`.                                                                                                                                              |
| `fixedUpdate(ctx)`             | fixed                   | Every physics tick while enabled. Runs in the `fixed` group, before `simulation`.                                                                                                          |
| `lateUpdate(ctx)`              | late                    | Every frame in the `late` group, after `simulation`. Use for camera/HUD follow-up.                                                                                                         |
| `onDisable(ctx)`               | simulation              | When `enabled` flips to 0. Also called once on destroy if the entity is enabled at that moment.                                                                                            |
| `onDestroy(ctx)`               | onDestroy callback      | Once, when the entity is removed. Runs after a final `onDisable` (if enabled). Registered as a destroy hook during setup.                                                                  |
| `onCollisionEnter(ctx, other)` | simulation              | First frame two non-sensor colliders touch. `other.entity` is the other entity.                                                                                                            |
| `onCollisionStay(ctx, other)`  | simulation              | Each frame a non-sensor contact continues (not the enter frame).                                                                                                                           |
| `onCollisionExit(ctx, other)`  | simulation              | Frame a non-sensor contact ends (`TouchEndedEvent`).                                                                                                                                       |
| `onTriggerEnter(ctx, other)`   | simulation              | Same as collision, but one side is a sensor collider (`Collider.isSensor === 1`).                                                                                                          |
| `onTriggerStay(ctx, other)`    | simulation              | Ongoing sensor overlap.                                                                                                                                                                    |
| `onTriggerExit(ctx, other)`    | simulation              | Sensor overlap ends.                                                                                                                                                                       |

Setup order on the first ready frame: `awake` -> `onEnable` (if starting enabled)
-> `start` (awaited) -> `ready` is set and per-frame hooks begin. Destroy order:
`onDisable` (if enabled) -> `onDestroy`.

The collision bridge treats a pair as a trigger if either side has
`Collider.isSensor === 1`. Pairs are tracked from enter to exit, so `Stay` fires
every simulation frame the contact continues.

## MonoBehaviourContext

Passed to every hook. Provides Unity-style access over the raw ECS: `state`,
`entity`, `deltaTime`, `object3d` (the loaded GLB root group or `null`),
`gameObject` (`{ id, name, tag, layer }`), `transform` getters
(`positionX/Y/Z`, `rotationX/Y/Z`, `scaleX/Y/Z`), `getComponent` /
`getComponentInChildren` / `getComponentInParent`, and coroutine helpers
`StartCoroutine` / `StopCoroutine` / `StopAllCoroutines`.

## Authoring patterns

### Pattern A: `script` attribute on a recipe

The `script` shorthand targets the `monoBehaviour` component, and the parser
auto-adds `monoBehaviour` to the entity. Works on any recipe that creates an
entity.

```xml
<GameObject name="merchant" place="at: 9 2" script="merchant.ts"></GameObject>
<GameObject role="enemy" script="enemies/wolf.ts"></GameObject>
```

### Pattern B: `<MonoBehaviour>` child element

The `MonoBehaviour` recipe is `merge: true`, so as a child it merges its
components onto the parent entity instead of creating a new one.

```xml
<GameObject name="chest" place="at: 16 -8">
  <MonoBehaviour script="chest.ts"></MonoBehaviour>
</GameObject>
```

### Combining both

Both forms write the same `monoBehaviour` component and per-entity script-file
map, so use whichever fits the scene. The attribute form is shorter and is the
dominant style in `simple-rpg`.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

XML placements from `simple-rpg/index.html`:

```xml
<!-- Attribute form: merchant NPC (press K to trade) -->
<GameObject name="merchant" place="at: 9 2; align-to-terrain: 0" script="merchant.ts"></GameObject>

<!-- Spawned enemy inside a DynamicSpawner -->
<DynamicSpawner count="6" seed="101" region-min="-75 0 45" region-max="75 0 150">
  <GameObject role="enemy" script="enemies/wolf.ts"></GameObject>
</DynamicSpawner>
```

Script module from `simple-rpg/src/scripts/chest.ts` (real exports, trimmed):

```ts
import { loadGltfToSceneWithAnimator, registerInteractionTarget } from 'vibegame';
import type { MonoBehaviourContext } from 'vibegame';

export function start(ctx: MonoBehaviourContext): void {
  registerInteractionTarget(ctx.state, ctx.entity, { label: 'Open chest', key: 'F' });
  void loadGltfToSceneWithAnimator(ctx.state, '/assets/meshes/treasure_chest_lod0.glb');
}

export function update(ctx: MonoBehaviourContext): void {
  const dt = ctx.deltaTime;
  const x = ctx.transform.positionX;
  // ... per-frame logic, reads input via isKeyDown('KeyF'), awards gold once
}
```

A creature factory returning `{ start, update, onDestroy }` (from
`simple-rpg/src/scripts/creature.ts`) shows `start` + `update` + `onDestroy`
together, loading an animated GLB in `start` and cleaning up in `onDestroy`.

## Known Limitations

- **One script per entity.** The script-file map is keyed by entity id and
  `monoBehaviour` is a single SOA slot, so only the last `script=` /
  `<MonoBehaviour>` wins. To compose behaviors, put the logic in one module.
- **No `onInteract` hook.** Interaction is implemented in `update` via input
  polling (`isKeyDown`) plus `registerInteractionTarget` / `unregisterInteractionTarget`.
- **Module load and `start` errors** are caught and logged; `ready` is set so
  the entity does not retry. `update` / `fixedUpdate` / `lateUpdate` / collision
  hooks are not wrapped in try/catch, so a thrown error propagates to the system
  runner for that frame.
- **GLB wait.** If the entity has `gltf-pending`, `start` is deferred until the
  GLB loads, so `object3d` is populated when `start` runs.
- **Glob resolution** matches by basename. Two files with the same basename are
ambiguous; the first match wins with a warning. Prefer unique names or
path-prefixed values (`enemies/wolf.ts`).
<!-- /LLM:EXAMPLES -->
