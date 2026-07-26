# VibeGame — path único de chão (árvores = inimigos)

Findings from fixing enemies that floated until the player approached (simple-rpg).

## Symptom

- Trees sit correctly after city pads/roads/rivers.
- Enemies hang in the air at boot; snap (or only look correct) when the camera gets close.

## Root cause (not DistanceCull alone)

Trees and enemies already shared `TerrainSpawnSystem` → `spawnTemplateAtTerrain`. Trees used profile `tree` (`alignToTerrain` + `groundAlign: aabb` + `TerrainSpawned` + resync). Enemies diverged:

1. `role="enemy"` mapped to `physics-box` (`groundAlign: none`, large `baseYOffset`).
2. XML often had `align-to-terrain="false"`.
3. `creature.ts` ran a **second** Y path (BVH / settle) — and `DistanceCull` paused `update` until near, so the script snap looked like “script starts when close.”

Workarounds that failed or must not return: `MonoBehaviour.settled`, loading gate `settle`, keeping culled scripts awake for feet Y.

## Canonical fix

| Layer | Rule |
| ----- | ---- |
| Profile | `creature` — same AABB ground path as `tree`, no tree scale jitter |
| Role map | `roleToProfile(enemy\|npc\|creature)` → `creature` |
| XML | `align-to-terrain` + `ground-align="aabb"` (or inherit profile) |
| Engine | mutation defer → spawn → AABB catch-up → `resyncTerrainSpawnedHeights` |
| Script | AI/anim only; optional awake slope via **same** `sampleTerrainSurface` + `TerrainSpawned.yOffset` |
| Loading | `assets` + `terrain` + `spawn` + `shaders` — no `settle` gate |
| DistanceCull | culled ⇒ skip hot `update`; setup still runs once |

## Regression — city goblins sinking (2026-07)

**Symptom:** north-gate warning goblins buried variously on pad skirt. Felt like navmesh/physics fight — was not. NavMesh = XZ only; no CharacterController on enemies.

**Root causes (engine):**

1. `sinkOffsetForSlope` for upright instances was **baked into** `TerrainSpawned.yOffset` at spawn → agents carried burial forever; steeper spawn ⇒ deeper bury.
2. `GameObject` + child `GLTFLoader` hid mesh URL → AABB/halfWidth fallbacks (`templateVisualUrl`).
3. AABB catch-up updated `Transform.posY` but **not** `yOffset` → resync dropped the lift.

**Canonical contract (not XML band-aids):**

| Field / mode | Rule |
| --- | --- |
| `yOffset` | Foot plant only (`baseYOffset` + AABB lift) |
| `halfWidth` | `>0` static edge-sink recomputed at current XZ; `0` for `DynamicSpawner` |
| `creature` profile | upright (`align=false`) + `ground-align=aabb` |
| Runtime Y | `applyTerrainSpawnedY` / resync — shared helper |

See `spawner/terrain-spawned-y.ts`, `components.ts` (`TerrainSpawned`).

## Honest state — enemy grounding / Rapier (2026-07)

**Previous answer:** nothing — enemies were `GameObject` + `GLTFLoader` only.

**Current (CCT):** `DynamicSpawner` enemies use `<Creature>` — kinematic `Rigidbody` + capsule `Collider` + `CharacterController` + `CharacterMovement`. Rapier CCT plants Y on the terrain chunk heightfield (same path as player). NavMesh with CCT feeds `desiredVel` only (no Transform XZ write); crowd agent teleports to Transform each tick. `goblin_collision.glb` still unreferenced.

Fake sampler paths stay banned for agents: AABB lift, `TerrainSpawned` on dynamics, per-frame `applyTerrainSpawnedY` in `creature.ts`, footprint-max plant. Spawn still seeds initial `Transform.posY` from the surface sample.

## Regression — sparse floating trees on dunes (2026-07)

**Symptom:** a few trees/cacti float near desert landmarks (~134, 53); most neighbours sit flush. Not AABB-only (would float many of the same mesh).

**Cause:** terrain chunks resolve mesh res with `maxBoostOverAabb(leaf)`. Spawn used `boostAt(point)`. Quiet density tile beside a featured neighbour (height variance / pad skirt) inside the same LOD leaf → visual mesh fine, spawn still on ~31 m lattice → float over dips.

**Fix:** `meshSurfaceResolutionForPoint` takes boost from `maxBoostOverAabb(deepestLeafAabb(...))` — same contract as chunk build. See `terrain/lod-select.ts`.

## Docs (source of truth)

- [`VibeGame/src/plugins/spawner/context.md`](../../VibeGame/src/plugins/spawner/context.md) — *Path único de chão*
- [`VibeGame/src/plugins/loading/context.md`](../../VibeGame/src/plugins/loading/context.md)
- [`VibeGame/src/plugins/entity-script/context.md`](../../VibeGame/src/plugins/entity-script/context.md) — *DistanceCull vs ground*
- Example: `VibeGame/examples/simple-rpg/public/world/creatures/enemies.xml`, `public/world/spawn/ring.xml`, `public/world/context.md`
