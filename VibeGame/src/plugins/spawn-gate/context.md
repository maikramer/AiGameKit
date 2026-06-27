# Spawn-Gate Plugin (context.md)

<!-- LLM:OVERVIEW -->

Holds dynamic entities in the air at their spawn Y until the terrain below them is both heightmap-decoded and backed by a Rapier heightfield collider, then snaps them onto the ground in a single fixed tick. Without this gate, gravity can accelerate a body during the gap between the visual surface appearing and the one-sided heightfield collider being built, tunnelling the entity through the floor. The latch is one-shot: once snapped, the entity is released and never re-gated.

<!-- /LLM:OVERVIEW -->

## Layout

```
spawn-gate/
├── context.md      # This file
├── index.ts        # Public re-exports
├── plugin.ts       # SpawnGatePlugin, spawnGateRecipe, spawnGateParser
├── components.ts   # SpawnGateComponent
└── systems.ts      # SpawnGateSystem, gateEntity
```

## Scope

- **In-scope**: Freezing a tagged entity at a hold Y, waiting on terrain data + collision readiness, sampling the surface Y, snapping the Transform and Rapier body to the ground.
- **Out-of-scope**: Terrain generation and heightfield build (see `terrain`), BVH raycast internals (see `bvh`), ongoing ground-follow after release (see `physics/character-ground`).

## Entry Points

- **plugin.ts**: `SpawnGatePlugin` definition, `spawnGateRecipe`, `spawnGateParser`.
- **systems.ts**: `SpawnGateSystem`, `gateEntity`.
- **index.ts**: Public re-exports.

## Dependencies

- **Internal**: `bvh` (`getBvhSurfaceHeight`), `terrain` (`getTerrainContext`, `getTerrainHeightAt`, `isTerrainDynamicsBlocking`), `transforms` (`Transform`), `physics` (`Rigidbody`, `Collider`, `getBodyForEntity`, `getBodyYForFeetAt`, `GROUND_CONTACT_SKIN`).
- **External**: None.

<!-- LLM:REFERENCE -->

### Component

#### SpawnGateComponent

- `ready` (ui8): 0 = gated (frozen in air), 1 = latched/released (snapped to ground). The latch is permanent for the entity's lifetime.
- `yOffset` (f32): world Y the entity is held at while the gate is open. Defaults to the entity's current `Transform.posY` (or the `y-fallback` attribute).
- `skinDistance` (f32): gap kept between the entity origin and the ground surface on snap. Defaults to `GROUND_CONTACT_SKIN`.

### System

#### SpawnGateSystem

- Group: `fixed` (runs on the physics tick, before `simulation`).
- Computes terrain readiness once per frame via `isTerrainGateReady`, which is `true` only when every terrain field is initialized, its optional heightmap has decoded into the sampler, and `collisionReady` is set (aggregated by `isTerrainDynamicsBlocking`).
- For each gated entity with `ready === 0`:
  - If terrain is not ready, calls `freezeAt` to pin the entity at `(x, yOffset, z)` and zero its linear velocity (so gravity cannot build up). The Rapier body translation is forced and `setLinvel({0,0,0})` is called.
  - If terrain is ready, samples the surface Y below the entity via `surfaceHeightAt` (a BVH raycast from `yOffset + 8m`, falling back to `getTerrainHeightAt`), adds `skinDistance`, and (when a `Collider` is present) routes through `getBodyYForFeetAt` so the body origin lands at the correct feet offset. Then it writes the new `Transform`, teleports the Rapier body (`setTranslation(..., wakeUp)`), and sets `ready = 1`.

### Recipe

- **SpawnGate**: parser-only recipe (no components added to the gate element itself). Attributes `target-entity`, `y-fallback`, `skin-distance`. The parser resolves the named target via `state.getEntityByName`, warns and no-ops if it is not found, and calls `gateEntity(state, targetEid, { yFallback, skinDistance })`.

### Helper

- `gateEntity(state, eid, opts?)`: attaches `SpawnGateComponent`, sets `ready = 0`, stores `yOffset` (from `opts.yFallback` or the current `Transform.posY`), and `skinDistance` (from `opts.skinDistance` or `GROUND_CONTACT_SKIN`). Use this for programmatic gating.

<!-- /LLM:REFERENCE -->
<!-- LLM:EXAMPLES -->

## Examples

```xml
<!-- Gate the player until the terrain underneath is collision-ready -->
<PlayerGLTF id="hero" pos="0 50 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
<SpawnGate target-entity="hero" y-fallback="50" skin-distance="0.05"></SpawnGate>
```

```ts
import { gateEntity } from 'vibegame';

// Programmatic gating (e.g. after spawning an entity at runtime)
gateEntity(state, enemyEid, { yFallback: 40, skinDistance: 0.05 });
```

### Known Limitations

- **Latch pattern**: once `ready === 1` the entity is never re-gated, even if the terrain later disappears or the entity moves. To re-gate, remove the `SpawnGateComponent` and call `gateEntity` again.
- **Document order**: the parser resolves the target by name at parse time. `<SpawnGate>` must appear after the target entity in the XML, otherwise it logs a warning and does nothing.
- **Single sample point**: the snap uses one BVH/heightmap sample at the entity's XZ. On steep slopes, overhangs, or uneven terrain the snap target may be imperfect, and there is no multi-sample smoothing here.
- **Fixed-group dependency**: the system runs in the `fixed` group and relies on `getBodyForEntity` returning a Rapier body. Entities without a `Rigidbody` are still snapped at the `Transform` level but cannot be teleported via the body API.

### See Also

- `terrain` plugin (`getTerrainContext`, `getTerrainHeightAt`, `collisionReady`) and `terrain/utils.ts` (`isTerrainDynamicsBlocking`).
- `bvh` plugin (`getBvhSurfaceHeight`) for the surface raycast.
- `physics/character-ground.ts` (`getBodyYForFeetAt`, `GROUND_CONTACT_SKIN`) for the feet-offset calculation.
- `examples/simple-rpg/src/main.ts` registers `SpawnGatePlugin` via `withPlugin`.

<!-- /LLM:EXAMPLES -->
