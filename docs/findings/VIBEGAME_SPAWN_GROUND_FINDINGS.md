# VibeGame — path de chão (spawn + CCT)

Findings from fixing enemies that floated / sank (simple-rpg).

## Symptom (historical)

- Trees sit correctly after city pads/roads/rivers.
- Enemies hung in the air at boot; looked correct only when the camera got close
  (script BVH snap + DistanceCull).

## Root cause (not DistanceCull alone)

Trees used profile `tree` (`alignToTerrain` + `groundAlign: aabb` + `TerrainSpawned` + resync). Enemies diverged:

1. `role="enemy"` mapped to `physics-box` with large `baseYOffset` hacks.
2. XML often had `align-to-terrain="false"`.
3. `creature.ts` ran a **second** Y path (BVH / settle / visual lift).

Banned workarounds: `MonoBehaviour.settled`, loading gate `settle`, per-frame
footOffset, `sinkOffsetForSlope`, magic `baseYOffset` / road float, script
`applyModelGrounding` / `applyVisualLift` / `recoverIfBuried`.

## Canonical contract (2026-07 cleanup)

| Layer | Rule |
| ----- | ---- |
| Statics (`tree`/`foliage`/`place`) | AABB foot plant + `TerrainSpawned.yOffset` + resync; `baseYOffset` default **0** |
| Dynamics (`creature`) | Spawn seeds surface Y; **CCT** owns Y after; profile `groundAlign: none`, `baseYOffset: 0` |
| Role map | `roleToProfile(enemy\|npc\|creature)` → `creature` |
| Assets | GLB feet at origin (`export_origin: feet`); no runtime sole fudge |
| Road ribbon | `y-offset` default **0** (decal; `polygonOffset` for z-fight) |
| Hero | `<SpawnGate>` once + CCT — no example HeroGroundSnap |
| Script | AI/anim only — no Y plant / lift / bury recovery |

See `spawner/terrain-spawned-y.ts`, `spawner/profiles.ts`, `road/plugin.ts`.

## Regression — city goblins sinking (slope bake)

**Root:** `sinkOffsetForSlope` baked into `TerrainSpawned.yOffset` → permanent burial.
**Fix:** removed (field `halfWidth` deleted). Fix assets / align; do not bury.

## Honest state — enemy grounding / Rapier

`<Creature>` = kinematic `Rigidbody` + capsule + `CharacterController` + `CharacterMovement`. Rapier CCT plants Y on the terrain heightfield (same path as player). NavMesh feeds `desiredVel` only. Fake sampler / visual-lift paths stay banned.

Skinned GLBs with soles **below** origin need **asset regen** (`export_origin: feet`), not script offsets. Player keeps measured `computePlayerFootAnchor` (root-local) for collider fit — not a magic constant.

## Regression — sparse floating trees on dunes

Spawn used `boostAt(point)` while chunks used `maxBoostOverAabb(leaf)`. Fix:
`meshSurfaceResolutionForPoint` uses deepest-leaf AABB boost — same as chunk build.

## Docs (source of truth)

- [`VibeGame/src/plugins/spawner/context.md`](../../VibeGame/src/plugins/spawner/context.md)
- [`VibeGame/src/plugins/loading/context.md`](../../VibeGame/src/plugins/loading/context.md)
- [`VibeGame/src/plugins/entity-script/context.md`](../../VibeGame/src/plugins/entity-script/context.md)
- Example: `VibeGame/examples/simple-rpg/public/world/creatures/enemies.xml`
