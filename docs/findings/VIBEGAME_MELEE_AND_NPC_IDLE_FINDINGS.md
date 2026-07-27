# VibeGame — Melee lunge overshoot + NPC auto-idle

Two engine fixes from simple-rpg gameplay reports (2026-07). Both are class
fixes in `VibeGame/src`, not example patches.

## 1. Bandit hits from far, then can't land (melee lunge overshoot)

**Symptom:** a bandit lands the first swing from well outside melee range, then
misses every subsequent swing while circling. Other enemies (wolf, goblin) feel
fine.

**Root cause (`plugins/rpg-ai/behaviour.ts`):** three compounding issues in the
lunge burst:

1. **Stale lunge direction.** `tickAttack` captures `inst.lungeDirX/Z` **once**
   at windup start (`tickAttack`, the `ready`→`windup` transition) and never
   re-aims during windup+lunge. The bandit's stale window is
   `lungeWindup + lungeDuration = 0.58 s`. A strafing hero (or the creature's
   own orbit ring) leaves the cached direction pointing along a tangent, so the
   burst overshoots past the player.
2. **One-sided standoff clamp.** `applyLungeMovement` only clamps when the
   creature gets **too close** (`pd < lungeStandoff`). There is no too-far
   clamp, so a tangent overshoot drops the creature outside the damage window
   and the end-of-lunge damage check misses.
3. **`LUNGE_HIT_FACTOR = 1.5`** made the damage window (`attackRange × 1.5`) 50%
   larger than the attack **trigger** (`attackRange`). The first swing lands
   "from far" because the damage window is generous relative to the trigger;
   subsequent swings miss because of (1)+(2).

**Fix (`behaviour.ts`):**
- `applyLungeMovement` now **re-aims `lungeDirX/Z` each frame** toward the
  target's current position (kills the tangent overshoot).
- Added a **too-far clamp**: if the burst step would leave the creature beyond
  `max(lungeStandoff, attackRange * LUNGE_HIT_FACTOR)`, pin it back to that ring
  so the end-of-lunge damage check always connects.
- `LUNGE_HIT_FACTOR` lowered `1.5 → 1.2`: the first swing no longer reads as
  landing from far (damage window now ~1.68 m vs the 1.4 m trigger instead of
  2.1 m). Safe to tighten because the re-aim + too-far clamp already guarantee
  repeat hits.
- **CCT sync (2026-07 follow-up):** `<Creature>` kinematic body owns XZ.
  Transform-only dashes were wiped by `PhysicsRapierSync` → first hit could
  still land on a one-frame Transform lie, then the body stayed put and later
  swings whiffed (worse with long bandit windup/standoff). Lunge now writes
  `Rigidbody.posX/Z` + `markRigidbodyPoseDirty`. Example `bandit.ts` matches
  goblin-wander lunge timing.
- **False "bandit" = archer (2026-07):** `archer.ts` reused the bandit GLB +
  `enemyType: 'bandit'` with `rangedTemplate` (lunge cooldown ≈ ∞). Far hit =
  arrow; up close FSM stayed in ATTACK and `pickClip` looped the melee
  `attack` anim with **no** melee damage. Spawner now uses `enemies/bandit.ts`;
  `archer.ts` is melee until a dedicated ranged asset exists.

**Boss safety:** bosses set `lungeWindup/Duration/Recovery = 0` — the re-aim is
a no-op at zero duration and the too-far clamp only engages when the creature
would end a step outside the ring, so the zero-lunge boss is unaffected.

**Tests:** `tests/unit/rpg-ai/melee-ai.test.ts` — all 178 pass, including
`applies attackDamage to the target` (creature at dist 2 < new hit window 3.6 m)
and `finishes an in-flight lunge instead of freezing`.

## 2. NPCs (scouts, city NPCs) play no idle animation

**Symptom:** NPC scouts (`npc_forest_survey`, …) and city NPCs
(`npc_healer`, `npc_guard`, `npc_elder`, `npc_blacksmith`) stand frozen in
bind/T-pose. Their GLBs ship 7 clips (`idle`, `walk`, …).

**Root cause:** these NPCs are declared as
`<GameObject><GLTFLoader url="…lod0.glb" …/></GameObject>` with **no `script=`**.
The `<GLTFLoader>` recipe attaches only `transform`/`gltfPending` — it never
creates a `GltfAnimator`. `GltfAnimationUpdateSystem` only **updates** registered
animators; it does not create them or autoplay. So script-less rigged NPCs
never animate. (Enemies and the merchant are fine — `creature.ts`/`merchant.ts`
create their own animators.)

**Fix (`plugins/gltf-xml/systems.ts`):** `maybeAutoPlayIdle(state, eid, url)`
runs in both `GltfXmlLoadSystem` load paths (LOD triple + single GLB) right
after `registerGltfRootGroup`:
- `loadGltfMasterTracked(url, 'background')` — **cache hit** (master already
  fetched for the visual clone; `_settledMasters` short-circuit means no
  re-download, no re-armed boot gate).
- Guard `if (!master.animations?.length) return;` — **excludes every static
  prop** (trees, rocks, market stalls, chests ship zero clips).
- Guard against script/player ownership: skip if the entity or its parent has
  `MonoBehaviour` / `PlayerController` / `PlayerGltfConfig` (so it never
  double-animates a creature's or the hero's skeleton).
- Creates a `GltfAnimator` on the lod0 child (or the group for single GLBs),
  registers it (`GltfAnimationState.registryIndex`), plays `idle` (fuzzy-matched
  via `resolveClipName`), and `state.onDestroy` cleans it up.

`GltfAnimationUpdateSystem` (already distance-culled) then ticks the mixer
automatically — no new system needed.

**Result:** 8 simple-rpg NPCs now idle (verified: scouts eids 487-490 carry
`gltf-animation-state.registryIndex` ≠ 0; visual confirmed relaxed idle pose,
not T-pose). Statics excluded by the animations guard.

**Caveat:** only the lod0 child animates; lod1/lod2 stay in bind when the LOD
switches far away — acceptable because that's the distance band where the update
system already half-rates/skips the mixer. Seamless cross-LOD idle (one animator
per lod child) would mirror `creature.ts attachLodAnimators` — not needed for the
reported bug.

## Skymap "circle that cuts" — open (not the sky)

Reported alongside the above. The `EquirectSky` plugin is a skybox infinite
dome (`scene.background` = equirect texture; in three.js this follows the
camera — **no culling, no circle**). Confirmed in-browser: from the city the
full dome is visible. The "circle that cuts as I walk" is therefore **not** the
sky; most likely candidates are dense biome **fog** (`FogExp2`, swamp
`fog-density 0.016` + rain) or **vegetation DistanceCull** (props popping in/out
in a radius). Needs the reporter to confirm the biome/zone to pin down.

## Docs (source of truth)

- `VibeGame/src/plugins/rpg-ai/behaviour.ts` — `applyLungeMovement`,
  `tickAttack`, `LUNGE_HIT_FACTOR`.
- `VibeGame/src/plugins/gltf-xml/systems.ts` — `maybeAutoPlayIdle`,
  `GltfXmlLoadSystem`.
- `VibeGame/src/plugins/gltf-anim/systems.ts` — `registerAnimator` /
  `GltfAnimationUpdateSystem`.
- `VibeGame/examples/simple-rpg/src/scripts/creature.ts` —
  presentation / AI only (Y = CCT; see `VIBEGAME_SPAWN_GROUND_FINDINGS.md`).
- `VibeGame/src/plugins/sky/systems.ts` — `EquirectSkyLoadSystem` (sky dome).
