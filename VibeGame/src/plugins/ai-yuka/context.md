# AI-Yuka Plugin (context.md)

<!-- LLM:OVERVIEW -->

Rich steering + light decision layer built on the [`yuka`](https://mugen87.github.io/yuka/)
game-AI library. One system (`YukaAgentSystem`) drives one queryable component
(`YukaAgentComponent`); the per-entity `yuka.Vehicle` and its steering behaviors
live in a per-`State` side table. When a `NavMeshAgent` is present, movement is
**delegated to the `navmesh` crowd**: yuka computes the goal point each frame,
recast resolves the path, and the crowd owns the `Transform` writeback. Without
a crowd agent (e.g. declarative `<NPC>` demos), the system writes planar
`Transform` directly.

<!-- /LLM:OVERVIEW -->

## Layout

```
ai-yuka/
├── context.md        # This file
├── index.ts          # Public re-exports
├── plugin.ts         # YukaAiPlugin (system + NPC recipe + component)
├── recipes.ts        # `<NPC>` recipe
├── components.ts     # YukaAgentComponent (SoA), YUKA_BEHAVIOR_* bitmask flags
├── perception.ts     # hasLineOfSight (BVH raycast) — reusable by any AI
├── vehicle-bridge.ts # Vehicle <-> ECS sync, behavior toggle, nav goal emit
├── decision.ts       # decide() utility-AI: situation → steering mask + target
├── systems.ts        # YukaAgentSystem (group: simulation)
└── context.ts        # per-State WeakMap side table of Vehicle runtimes
```

## Scope

- **In-scope**: steering (seek/arrive/pursuit/evade/flee/wander/separation/flock),
  planar goal emission to the navmesh crowd (or Transform fallback), same-faction
  neighbor population, line-of-sight perception, a light utility-AI decision helper,
  declarative `<NPC>` recipe.
- **Out-of-scope**: damage / health (see `combat`), navmesh pathfinding internals
  (see `navmesh`), the melee lunge FSM (see `rpg-ai`). This plugin **composes**
  with `rpg-ai`: yuka handles _getting there as a pack_, `rpg-ai` handles the
  _attack lunge_ once in range. Games choose per-creature which drives.

## Why yuka + navmesh

yuka ships flocking, pursuit, arrive, and related behaviors as drop-in steering.
But yuka has no pathfinder that respects the baked navmesh, so a pure-yuka mover
walks through walls. The bridge resolves this: **yuka decides where, recast
decides how**. `vehicle.update(dt)` produces a desired planar position;
`emitNavTarget` forwards it to `setAgentTarget`; the crowd moves the entity and
writes `Transform`. When no `NavMeshAgent` is driving the entity, the system
writes planar `Transform` itself so simple `<NPC>` scenes still move.

## Entry Points

- `plugin.ts`: `YukaAiPlugin` (in `DefaultPlugins`).
- `recipes.ts`: `npcRecipe` (`<NPC>`).
- `systems.ts`: `YukaAgentSystem`.
- `vehicle-bridge.ts`: `createYukaRuntime`, `syncVehicleFromTransform`,
  `applyBehaviorMask`, `bindTarget`, `emitNavTarget`, `TargetProxy`.
- `decision.ts`: `decide`, `applyDecision`.
- `perception.ts`: `hasLineOfSight`, `DEFAULT_VISION_BLOCK_LAYERS`.

## Component

### YukaAgentComponent

- `active` (ui8): 0 skips the entity entirely (sleeping / dead).
- `behavior` (ui32): OR of `YUKA_BEHAVIOR_*` flags.
- `maxSpeed` / `maxForce` (f32): copied into the Vehicle each frame.
- `targetEid` (ui32): focus entity (the hero). 0 = static `targetX/Z` / wander.
- `faction` (ui8): used by separation/flock (allies only).
- `targetX` / `targetZ` (f32): static goal when `targetEid === 0`.

### Recipe `<NPC>`

XML attrs: `behavior`=`seek|wander|flee` (mapped to bitmask flags), `max-speed`,
`max-force`, `target-x`, `target-z`, `target-eid`. Components: `transform`,
`yukaAgent`, `meshRenderer` (placeholder sphere).

### Side table (per-State WeakMap)

`YukaRuntime = { vehicle, behaviors: Map<id, Behavior>, lastMask, lastTargetEid }`.
Access via `getYukaRuntime`, `getYukaRuntimeMap`, `deleteYukaRuntime`. The
`yuka.Vehicle` is not serializable/queryable, so it cannot live in a typed array.

## Decision layer (`decide`)

A pure, allocation-free utility function: given `(eid, targetEid, profile)` it
returns a `{ mask, targetEid }` result following a fixed priority
(flee → kite/evade → pursue → arrive-and-hold). Game code calls `decide` then
`applyDecision` in its entity-script `update`. `CreatureDecisionProfile` exposes
the knobs that map to how the creature _feels_ (flee threshold, stand-off range,
kite vs body-block, separate vs flock).

### Known Limitations

- yuka is a runtime engine dependency (added under `dependencies`); it is
  bundled into `dist`, not externalized.
- `vehicle.updateOrientation = false`: heading is owned by presentation
  (`Transform.eulerY`), not yuka's quaternion — consistent with the melee FSM.
- Flocking/separation iterate the agent query each frame (O(n²) over awake
  agents within `NEIGHBOR_RADIUS`). Fine for tens of creatures; for hundreds,
  throttle or spatial-partition (yuka ships `CellSpacePartitioning`).
- `hasLineOfSight` casts one BVH ray at a fixed eye height; it does not model
  vision cones or alertness decay (add per-creature if needed).

### See Also

- User-facing guide: [`docs/AI.md`](../../../docs/AI.md).
- `navmesh` (`setAgentTarget`, `NavMeshAgent`, `isNavMeshReady`).
- `bvh` (`castBvhRay`, `getBvhSurfaceHeight`).
- `rpg-ai` (`runMeleeAiFrame` — the melee lunge FSM; compose with this plugin).
- `combat` (`Health`, `FactionComponent`).
