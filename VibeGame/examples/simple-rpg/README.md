# Simple RPG Demo: Crystal Vale (AiGameKit monorepo pipeline)

End-to-end example of the **AiGameKit monorepo workflow**: describe assets in `game.yaml` + `manifest_full.csv`, generate **GLBs** (Text3D + Paint3D), optional **rigging** (Rigging3D) and **animation** (Animator3D), **audio** (Text2Sound), **sky** (Skymap2D), **handoff** to `public/assets/`, and run a playable **VibeGame** scene.

The demo grew past a bare scene into a small but complete action RPG. The world is a central walled city surrounded by **four biome regions** (dark forest, desert, swamp, frozen peaks), each with its own enemies, a boss, and four quest NPCs (three jobs + one survey). That is **16 biome quests** and **4 bosses**, plus **4 repeatable city-watch bounties** on the plaza notice board, a blacksmith job at the forge, a chapel healer, XP and skill progression, a gold economy with a merchant, bombs, consumables, and abilities. Mark a landmark with **F** and the plaza campfire (**H**) will send you back there; death respawns at the nearest gate or marked landmark. Save and load live in the pause menu's Options tab.

On the engine side it exercises the full RPG plugin stack: combat, inventory, progression, economy, status effects, melee-AI (a shared FSM that drives every creature and boss), pause coordination, spawn gating, navmesh, save/load, and i18n. Declarative **sky** (`<EquirectSky url="…">`) and **audio** (`<AudioMixer>` + `<MusicLayer>`, `defineSoundBank` + `playSound`, with `resume-audio-on-user-gesture`) round it out.

**Português:** demo completa do pipeline do monorepo AiGameKit. O GameAssets batch gera GLBs, áudio e imagens; o handoff copia para `public/`; o VibeGame carrega GLBs via `<GLTFLoader>` / `<PlayerGLTF>`, céu equirect com `<EquirectSky>`, e SFX nomeados via `defineSoundBank` / `playSound` (ver [`docs/AUDIO.md`](../../docs/AUDIO.md)). O demo é um RPG completo: **4 biomas** (floresta sombria, deserto, pântano, picos gelados), **16 quests de bioma** (3 jobs + 1 traçado por cunha), **4 recompensas repetíveis** no quadro da praça, **ferreiro** e **curandeira** na cidade, **4 chefes**, XP e árvore de habilidades, economia de ouro com comerciante, bombas, consumíveis e habilidades. Marcos da Nota viram viagem rápida na fogueira da praça (**H**) e respawn na morte. Save/load ficam no menu de pausa (aba Opções). Stack de plugins: combate, inventário, progressão, economia, efeitos de status, IA melee, pausa, gating de spawn, navmesh, save/load e i18n (EN/PT).

## Getting started

The 3D assets (GLB meshes, images, textures, sky, audio) are large binary
blobs, so they are **not committed to git**. They live once in the **shared
pool** ([`examples/shared-assets/public/assets/`](../shared-assets/README.md))
— every example reads them through the `vibegame({ sharedAssets })` plugin —
and a fresh clone restores them from a pinned GitHub Release on demand. Only
game-specific media (`icons/`, `particles/`, `terrain/`) stays in this
example's `public/assets/`:

```bash
npm install        # or: bun install
npm run dev        # predev runs scripts/fetch-assets.mjs automatically
```

`scripts/fetch-assets.mjs` downloads the bundle pinned in `assets.lock.json`,
verifies its sha256 and installs it **fill-if-missing** — the shared pool and
the game-specific media alike. Nothing is ever overwritten (pool and local
work are canonical; the release only fills gaps), and the download itself only
runs when nothing is present yet — `--force` re-downloads but still never
overwrites. Run it directly with `npm run setup` if needed. To bump the
assets, regenerate them with the GameAssets pipeline, upload a new release,
and update `assets.lock.json` (`version` + `url` + `sha256`).

## What is in the scene

| Element                                    | Source / Plugin                          | How it loads                                                                                                                            |
| ------------------------------------------ | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Terrain (2 km, quadtree LOD)               | Built-in `<Terrain>`                     | Declarative in `index.html` shell (`world-base`; heightmap under `public/assets/terrain/`)                                              |
| Sky IBL + background                       | Skymap2D (equirect PNG) + `sky` plugin   | `<EquirectSky>` in `public/world/environment.xml`                                                                                       |
| NavMesh                                    | `NavMeshPlugin`                          | `<NavMesh>` in `index.html` (`world-base`)                                                                                              |
| World layout (city, biomes, spawn)         | `<Include>` + city-layout recipes        | Fragments under `public/world/` (see that folder’s `context.md`)                                                                        |
| Player (animated GLB + WASD)               | Built-in `<PlayerGLTF>`                  | **LOD0 only:** `<PlayerGLTF name="hero" model-url="/assets/meshes/characters/hero_lod0.glb">` (no `lod1-url` / `lod2-url`)              |
| Third-person camera + post-fx              | Built-in `<ThirdPersonCamera>`           | Declarative (bloom, vignette, SSAO, AGX tonemap; `<PostFxDebugToggle>` cycles effects on 1-6)                                           |
| Audio mixer + layered music                | Engine audio plugin                      | `<AudioMixer>` + `<MusicLayer layer="explore\|battle">` (crossfaded by biome)                                                           |
| Central walled city                        | `<Composition>` + city-layout + includes | Walls ±32, 4 gates, districts under `public/world/cities/discordia/`                                                                    |
| Settlement pad                             | `terrain` plugin                         | `<TerrainPad size="96 96" falloff="16" corner-radius="14">` — soft flatten under the city (not a hard plateau)                          |
| Spawn exclusion (city)                     | `spawner` plugin                         | `<SpawnExclusion at="0 0" radius="42">` (sync `villageZones` in `src/main.ts`)                                                          |
| Peri-urban skirts                          | `cities/discordia/skirts.xml`            | Gardens, bushes, market clutter just outside the walls                                                                                  |
| City merchant                              | Entity script                            | `<GameObject name="merchant" script="merchant.ts">` (press **K** to trade)                                                              |
| City notice board                          | Entity script                            | `<GameObject name="notice_board" script="notice-board.ts">` (press **F** — repeatable watch bounties)                                   |
| City healer                                | Entity script                            | `<GameObject name="npc_healer" script="healer.ts">` (press **F** — full heal for gold)                                                  |
| City blacksmith                            | `quests` plugin                          | `<DialogueNPC dialogue-id="city_stone">` on `npc_blacksmith` (press **F**)                                                              |
| Plaza campfire                             | Entity script                            | `<GameObject name="campfire" script="campfire.ts">` (press **H** — rest + travel to marked Nota landmarks)                              |
| Plaza well                                 | Entity script                            | `<GameObject name="well" script="well.ts">` (press **F** — sip heal, short cooldown)                                                    |
| Forge anvil                                | Entity script                            | `<GameObject name="anvil" script="anvil.ts">` (press **K** — 2 stone + 1 wood → bomb)                                                   |
| Watchtower guard                           | Entity script                            | `<GameObject name="npc_guard" script="watch-guard.ts">` (press **F** — pin the four gates on the compass)                               |
| Building interiors                         | `interiors.xml` + `building-portal.ts`   | **F** at any city door → authored room (houses, chapel, forge, barn, longhouse, market). **F** at the exit marker returns to that door. |
| Interactables (rune pillar, shrine, chest) | Entity scripts + trimesh colliders       | `<GameObject script="…">` (press **F** to interact)                                                                                     |
| Valley carpet (grass / flowers)            | Smart `<Vegetation>`                     | Ring ±58 (`spawn/ring.xml`, high `density-per-km2`); biomes in `vegetation/crystal-vale.xml`; wind on                                   |
| Biome clutter (props / debris)             | `<StaticSpawner>` / `<Composition>`      | `public/world/clutter/crystal-vale.xml` — mushrooms, bushes, crates, rocks outside ±58                                                  |
| Ambient atmosphere FX                      | `<ParticleSystem>`                       | `public/world/atmosphere/ambient-fx.xml` — desert `ground-dust`, forest fireflies, witch smoke                                          |
| Static resources (trees, rocks, cacti)     | `<StaticSpawner>` + `<ResourceNode>`     | Valley ring ±58; biomes outside ±58. Fall trees: `*_lod0` (`Stump`+`Top`) + `*_stump_collision`; rocks keep full hull                   |
| Biome enemies (animated)                   | `<DynamicSpawner>` + scripts + LOD×3     | Same ground path as trees (`profile="creature"` / AABB); child LOD×3; `creature.ts` = AI/anim only (no boot Y snap)                     |
| Biome regions (fog/ambient/BGM/clouds)     | `biomes` + `weather` plugins             | `<BiomeRegion … clouds rain>` x4; Weather drifts clouds globally, biomes override coverage                                              |
| Quest NPCs + dialogue (16 biome + 1 smith) | `quests` plugin                          | `<DialogueNPC>` inside `<Composition>` + `<DialogueBalloon>`                                                                            |
| Bosses (4)                                 | Scripts + LOD×3 GLTFLoader               | Same LOD pattern as enemies: witch, sand-worm, bog-warden, ogre (`boss.ts`)                                                             |
| HUD widgets                                | `hud` plugin                             | `<HealthBar>`, `<XpBar>`, `<ResourceChip>` (gold/wood/stone), `<Minimap>`, `<Compass>`, `<BossBar>`, `<InteractionPrompt>`              |
| Pause menu (tabbed modal)                  | `hud` plugin                             | `<TabbedModal key="q">` with Skills, Inventory, Options, Quests tabs                                                                    |
| Particles                                  | Engine particle system                   | Combat/destructible bursts + ambient `ground-dust` / fireflies (Kenney sprites under `public/assets/particles/`)                        |
| Save / Load                                | `SaveLoadPlugin`                         | Buttons in the pause menu **Options** tab (localStorage + msgpackr)                                                                     |
| Localized messages (EN/PT)                 | `i18n` plugin                            | `loadDictionary` + auto-detected locale                                                                                                 |

## Mesh LOD policy (hero vs enemies)

| Role                 | Mesh URLs in `index.html`                                  | Why                                                                                                                                                                                                                                                                      |
| -------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Hero**             | `hero_lod0.glb` only on `<PlayerGLTF>`                     | Always near camera; LOD1/2 not wired (keeps one skinned animator path).                                                                                                                                                                                                  |
| **Enemies / bosses** | `*_lod0` + `lod1-url` + `lod2-url` on child `<GLTFLoader>` | Distance LOD via `GltfLodSystem` (near≈35, mid≈85). Scripts (`creature.ts`) wait for the child group (`getGltfRootGroup`), attach `GltfAnimator` with `root` = each LOD clone, sync clips across levels. Fallback: script loads `modelUrl` if XML visual missing (~3 s). |

Canonical paths: `/assets/meshes/{id}_lod0.glb` (not `_rigged_animated` for runtime — alias optional). Pipeline must ship **animated** lod0 when manifest has `rig`+`animate`; see [`docs/findings/MESH_PIPELINE_FINDINGS.md`](../../../docs/findings/MESH_PIPELINE_FINDINGS.md) (promote/resume).

**Português:** herói só LOD0; inimigos/chefes usam LOD0/1/2 no XML; `creature.ts` adopta o visual e anima todos os níveis.

## Engine features demonstrated

| Feature            | Plugin                      | Usage in this demo                                                                                                                                                           |
| ------------------ | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Combat             | `CombatPlugin` + `melee.ts` | Melee at **~35%** of attack clip (whoosh + arc damage); bombs; enemy feedback / numbers                                                                                      |
| Inventory          | `InventoryPlugin`           | Stackable items (wood, stone, bomb, potions, quest rewards); `<InventoryTab>` in pause menu                                                                                  |
| Progression        | `ProgressionPlugin`         | XP on kill, level up, stat modifiers (Vitality, Strength, Agility); `<SkillsTab>` in pause menu                                                                              |
| Economy            | `EconomyPlugin`             | Gold counter, merchant buy/sell, chapel healer (full heal for gold), notice-board bounties                                                                                   |
| Status effects     | `StatusEffectsPlugin`       | Poison, buffs (consumables and abilities apply them)                                                                                                                         |
| RPG melee AI       | `RpgAiPlugin`               | One engine FSM (`runMeleeAiFrame`) drives every creature and boss: detect, chase, lunge, strafe, enrage                                                                      |
| Pause coordination | `PauseCoordinatorPlugin`    | Freezes simulation while the pause modal is open                                                                                                                             |
| Spawn gating       | `SpawnGatePlugin`           | Final boss stays dormant until its gate condition clears                                                                                                                     |
| NavMesh            | `NavMeshPlugin`             | Pathfinding surface for chasing AI                                                                                                                                           |
| Save / Load        | `SaveLoadPlugin`            | Options tab Save/Load buttons (localStorage + msgpackr); merchant progress serializer                                                                                        |
| i18n               | `I18nPlugin`                | Auto-detect PT/EN; HUD, modal, and controls text localized                                                                                                                   |
| Audio              | Engine audio                | Bank + spatial cull; boot `preloadSounds` deferred until `resume-audio-on-user-gesture`; `originEid` on combat SFX; `<AudioMixer>` / biome `<MusicLayer>`; `?profiler=audio` |
| Spawners           | `spawner` plugin            | `<StaticSpawner>` / `<DynamicSpawner>`; trees = AABB + `TerrainSpawned` resync; enemies = `<Creature>` CCT                                                                   |
| Particles          | Engine particles            | Destructible bursts (`dust`/`leaves`/…) + ambient `ground-dust` sheet / fireflies                                                                                            |
| Weather            | `weather` plugin            | `<Weather>` drifting clouds; biome `clouds`/`rain` via `setEnvironmentClouds` / `setEnvironmentRain`                                                                         |
| Terrain            | `terrain` plugin            | Heightmap with quadtree LOD, per-chunk Rapier heightfield, biome splat + **noise-sand** fBm overlay                                                                          |
| Biome detection    | `biomes` plugin             | Fog/ambient/terrain texture/BGM + optional `clouds`/`rain` overrides on `<BiomeRegion>`                                                                                      |

> Note: creature locomotion may use `YukaAiPlugin` (in `DefaultPlugins`; see
> [`docs/AI.md`](../../docs/AI.md)); melee lunges still go through the engine
> melee-AI FSM from `RpgAiPlugin`.

## Pipeline (step by step)

### 1. Review the plan

The scene layout and assets were planned with **`gameassets dream`** (dry-run, no GPU) and then refined per biome. Every pack manifest (characters, desert, forest, infra, interiors, props, props-rpg, swamp, terrain, vehicles, village) is canonical in
[`examples/shared-assets/manifests/`](../shared-assets/README.md) — one pool,
shared by all examples, consumed via `vibegame({ sharedAssets })` in
`vite.config.ts`. No copies and no symlinks inside the examples; only
game-specific media and manifests live here:

```
sample-gameassets/
  game.yaml                      # GameAssets profile — só config (estilo, ferramentas, presets)
  manifests/
    audio.yaml                   # BGM/SFX (Text2Sound) → public/assets/audio/ (local do jogo)
examples/shared-assets/          # pool canónico
  game.yaml                      # perfil dos packs partilhados
  manifests/<pack>.yaml          # 1 manifesto por pack (binários em public/assets/)
  public/assets/{meshes,images,textures,sky}/   # servido a todos os exemplos
# Binários do pool: não commitados; fresh clone restaura via `bun run setup`
# (Release pinado) ou regen GPU. Só JSON metadata pequeno é commitado.
```

### 2. Generate assets (requires GPU)

From the shared pool (`resume` é idempotente — só gera o que falta):

```bash
cd VibeGame/examples/shared-assets

# 2D images + 3D meshes + PBR textures + rigging + animation
# (um comando por pack; `ls manifests/` lista todos)
gameassets resume --profile game.yaml --manifest manifests/characters
gameassets resume --profile game.yaml --manifest manifests/props-rpg
# ... manifests/{desert,forest,infra,interiors,props,swamp,terrain,village}

# Audio específico deste jogo (Text2Sound) — corre a partir do exemplo:
cd ../simple-rpg/sample-gameassets
gameassets resume --profile game.yaml --manifest manifests/audio

# Sky (separate CLI): write directly into the pool's sky/
cd ../../shared-assets
skymap2d generate "bright blue sky with soft clouds over green plains, equirectangular 360" -o public/assets/sky/sky.png
```

### 3. Handoff

O handoff corre a partir do pool e escreve nele (os exemplos leem via plugin;
não há passo de cópia para o jogo):

```bash
cd VibeGame/examples/shared-assets
gameassets handoff \
  --profile game.yaml \
  --manifest manifests/characters \
  --public-dir public \
  --with-textures
# repetir por pack (passar um de cada vez)
```

Layout resultante (pool vs. exemplo):

```
examples/shared-assets/public/assets/     # pool — servido a todos os exemplos
  meshes/             # final GLBs (lod0/… + vegetation/ bpy carpet): não commitados
  images/             # Text2D PNGs: não commitadas
  textures/           # diffuse/PBR textures used by terrain and biomes
  sky/sky.png
simple-rpg/public/assets/                 # media específica do jogo
  audio/              # Text2Sound WAV/OGG
  icons/              # UI icons
  particles/          # Kenney CC0 sprites (flame, smoke, spark, …)
  terrain/            # terrain.ahgt + terrain.json
  gameassets_handoff.json   # URLs + bloco `precompute` por asset (ver abaixo)
```

**Colisores pré-calculados (`precompute`):** o handoff inline o sidecar
`{id}_precompute.json` (emitido pelo batch no fim do master pipeline via
`aigamekit-lab precompute`) nas rows do `gameassets_handoff.json` — cápsula
do tronco (árvores) / cilindro (pedras), com raio derivado só do tronco. A
engine lê esse manifest (`PrecomputePlugin`) e monta `collider="shape:
precompute"` sem baixar `*_collision.glb`; o carve do NavMesh é procedural
(prisma de 8 lados) e o bake não espera downloads. Sem manifest → fallback
AABB-fit (comportamento antigo). Docs:
[`src/plugins/asset-precompute/context.md`](../../src/plugins/asset-precompute/context.md).

`fetch-assets` fills the pool in fill-if-missing mode, so a release tarball never restores Kenney stubs over the bpy vegetation carpet.

### Vegetation (smart carpet)

Ground carpet uses engine `<Vegetation smart="1">` (roles `grass` → `plant` → `flower` share cluster hubs). Docs: [`src/plugins/vegetation/context.md`](../../src/plugins/vegetation/context.md), asset pipeline: [`scripts/README_VEGETATION.md`](scripts/README_VEGETATION.md).

| Patch                           | XML                                        | Region (approx.) |
| ------------------------------- | ------------------------------------------ | ---------------- |
| Valley ring                     | `public/world/spawn/ring.xml`              | periurban ±58    |
| Forest / desert / swamp / peaks | `public/world/vegetation/crystal-vale.xml` | biome boxes      |

Regenerate GLBs (needs Animator3D `bpy` venv):

```bash
cd VibeGame/examples/simple-rpg
npm run generate-vegetation
```

### 4. Run the game

```bash
cd VibeGame/examples/simple-rpg
bun install   # first time only
bun run dev   # http://localhost:3011
```

### Without GPU (just the engine)

The scene still runs without GLBs. You see the terrain, the central city geometry (walls, houses, well, campfire are primitive boxes/cylinders), HUD widgets, the pause menu, and quest NPCs (simple box/sphere figures). Enemy and creature GLBs are missing, so combat targets log load warnings. Missing sky/terrain textures fall back to solid colors.

## Controls

| Input            | Action                                                          |
| ---------------- | --------------------------------------------------------------- |
| W A S D          | Move (relative to camera)                                       |
| Shift            | Sprint                                                          |
| Space            | Jump                                                            |
| J                | Attack / harvest — SFX+hit no pico do clip (~35%), não no press |
| F                | Interact (doors, NPCs, chests, shrines, readables)              |
| K                | Trade with the merchant                                         |
| B                | Bomb (tap to drop, hold to aim and lob)                         |
| V                | Cycle held weapon (sword / axe / spear)                         |
| 1                | Use potion (heal)                                               |
| 2                | Use antidote (cure poison)                                      |
| C                | Dash                                                            |
| E                | Heal (ability)                                                  |
| R                | Power Strike (ability)                                          |
| H                | Campfire: rest + travel to marked Nota landmarks                |
| Q                | Pause menu (Skills / Inventory / Options / Quests)              |
| W / S            | Navigate open menus (shop, campfire, notice board)              |
| L                | Close open menus (shop, campfire, notice board; Esc also works) |
| Right mouse drag | Orbit camera                                                    |
| Mouse wheel      | Zoom                                                            |

Save and load are **not** bound to keys anymore. They live as buttons in the
pause menu's **Options** tab (open with **Q**).

## Biomes and quests

The world spans **four biome regions** radiating from the central walled city. Each cardinal gate leads into a distinct biome with its own atmosphere, enemy types, quest NPCs, and a boss at its far end.

**City planning (contained):** walls ±32 → exclusion r=42 → `TerrainPad` 96×96 (falloff 16) → valley resources/vegetation in ring ~±36–58 (`spawn/ring.xml`) → deep biome spawners outside ±58. Fog polygons still start near ±28 so the city edge already feels like the biome; props/enemies stay farther out so the settlement does not sprawl. Cardinal gate skirts (pad falloff + flatten roads) rely on terrain **density boost** + density-aware spawn height — without that, oaks/grass float at west/east exits (engine: `meshSurfaceResolutionForPoint` in terrain/spawner plugins).

| Biome                   | Atmosphere (fog ≥) | Deep spawn (≥) | Atmosphere                                     | Enemies                   | Boss         | Quest NPCs (dialogue-id)                       |
| ----------------------- | ------------------ | -------------- | ---------------------------------------------- | ------------------------- | ------------ | ---------------------------------------------- |
| **Dark Forest** (north) | z > 28             | z ≥ 58         | Dark fog, `clouds=0.85`, fireflies             | wolf, shade               | Witch        | forest_wolves, forest_shades, forest_darkwood  |
| **Desert** (east)       | x > 28             | x ≥ 58         | Sandy fog, `clouds=0.25`, `ground-dust` sheets | scorpion, bandit          | Sand Worm    | desert_scorpions, desert_bandits, desert_ruins |
| **Swamp** (south)       | z < -28            | z ≤ -58        | Murky fog, `rain=0.35`                         | bogling                   | Bog Warden   | swamp_boglings, swamp_bogwarden, swamp_bogmoss |
| **Frozen Peaks** (west) | x < -28            | x ≤ -58        | Cold fog, sparse carpet                        | goblin, slime, frost wolf | Ogre (final) | peaks_goblins, peaks_frost, peaks_ogre         |

Each biome is declared via `<BiomeRegion polygon="[x,z;x,z;...]">` in `public/world/environment.xml`. The `biomes` plugin detects the player's position and crossfades fog, ambient, terrain texture, BGM, and optional **cloud/rain** overrides. Ambient particles live in `atmosphere/ambient-fx.xml`; extra props in `clutter/crystal-vale.xml`. Engine notes: `src/plugins/weather/context.md`, `biomes/context.md`, `particles/context.md`.

**Quest system:** 12 NPCs (3 per biome) offer quests loaded from `src/data/quests/*.json`. Quests are either kill-N-enemies or collect-N-resources. Walk up to an NPC and press **F** to open the dialogue balloon, accept the quest, then track progress in the **Quests** tab of the pause menu. Quest state persists via `SaveLoadPlugin`.

## Bosses

Four bosses guard the far end of each biome. The first three (Witch, Sand Worm, Bog Warden) are placed via `<GameObject script="bosses/*.ts">` and spawn active. They share the same engine melee-AI FSM as regular creatures but with higher HP, wider detect range, strafing, and an enrage phase at low health.

| Boss       | Biome        | Script                 | Notes                                             |
| ---------- | ------------ | ---------------------- | ------------------------------------------------- |
| Witch      | Dark Forest  | `bosses/witch.ts`      | Elite, back of the forest                         |
| Sand Worm  | Desert       | `bosses/sand-worm.ts`  | Elite, deep desert                                |
| Bog Warden | Swamp        | `bosses/bog-warden.ts` | Elite, far swamp                                  |
| Ogre       | Frozen Peaks | `boss.ts`              | **Final boss**, gated until the peaks are cleared |

The final boss (Ogre) is the `<BossBar>` target in the HUD. It stays dormant until the frozen-peaks biome is cleared, then activates with an intro roar, relentless pursuit (huge detect range, never leashes), strafing, and an enrage phase below 30% HP. Defeating it ends the run with a "BOSS DEFEATED!" message and a gold drop.

## Progression, economy, and abilities

**XP and levels.** Killing enemies awards XP (`addXp` in `CombatFeedbackSystem`). Leveling up grants stat points you can spend in the **Skills** tab (pause menu) on three lines: Vitality (max HP), Strength (attack damage), and Agility (move speed). The `HeroStatsSystem` resolves all active modifiers every frame.

**Gold economy.** Enemies drop gold on death. Spend it at the city merchant (**K**): buy a speed ring (permanent move-speed multiplier) and sword upgrades (flat damage bonus per level, folded into bomb damage too). The merchant progress serializer persists `ringOwned` and `swordLevel` so a reload can't re-grant the ring or reset upgrades.

**Bombs and abilities.** Buy bombs from the merchant, then throw them with **B** (tap to drop at your feet, hold to aim an arc at the nearest enemy). Abilities live on a cooldown bar: **C** dash, **E** heal, **R** power strike (`src/game/abilities.ts`).

**Consumables.** Potions (**1**, heal) and antidotes (**2**, cure poison) sit on a hotbar (`src/game/consumables.ts`). Quest rewards and harvested resources (wood, stone, quest items) stack in the inventory.

## Combat melee timing

Hero **J** (and harvest on destructibles) does **not** play swing SFX / land damage on the key edge. `src/game/melee.ts` schedules at **`SWING_IMPACT_FRACTION = 0.35`** of the active attack clip (`sword` / `axe` / … via `getPlayerAttackClip()`), matching engine `ATTACK_IMPACT_FRACTION` and Destructible `impactFraction`. Quaternius-style clips (~1.5 s) peak the cut ~25–40%; 0.7× duration felt a beat late.

Keep one-shot WAVs short (~0.5–1.2 s). Long Text2Sound tails (~20–30 s) make a single swing sound like endless combat — see `regen_sounds.py` and [`docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md`](../../../docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md).

## Profiling

The demo registers the engine `ProfilerPlugin` (plus `DebugPlugin`). Find CPU bottlenecks and rogue SFX without guessing:

| Input                 | Action                                                             |
| --------------------- | ------------------------------------------------------------------ |
| **`P`**               | Toggle the in-game profiler panel (Systems + **Audio** tabs)       |
| **`Shift+P`**         | Cycle `sample` ↔ `deep` (User Timing marks for Chrome Performance) |
| **`Pause`**           | Freeze / unfreeze the snapshot                                     |
| **`?profiler=1`**     | Open the profiler on load (`?profiler=deep` for marks)             |
| **`?profiler=audio`** | Open **Audio** tab (active plays, origins, preload vs gameplay)    |
| **`?profiler=world`** | Open **World** tab (player pos, camera, nearby entities)           |
| **`?`**               | Debug overlay (FPS / entity counts)                                |
| **`G`**               | stats-gl GPU/CPU/draw-call panel                                   |

Console / Playwright:

```js
__VIBEGAME__.profiler.top(15)
__VIBEGAME__.profiler.snapshot()
__VIBEGAME__.audio.snapshot()   // plays, topOrigins, cull skips
__VIBEGAME__.profiler.worldSnapshot()  // player / camera / nearby
__VIBEGAME__.profiler.download()
```

Filter the systems list for `terrain`, `vegetation`, `render`, or `rpg/` (game-side spans on hero snap, combat feedback, BGM). Audio E2E: `playwright/audio-spatial.spec.ts`.

## World maps (`public/world/`)

`index.html` is a **shell**: hero, terrain, UI, and `<Include src="/world/…">` tags.
Edit the fragment for the domain you care about:

| Edit this                                     | To change                                                    |
| --------------------------------------------- | ------------------------------------------------------------ |
| `public/world/cities/discordia.xml`           | City shell (`SpawnExclusion` r=42 + `TerrainPad` + Includes) |
| `public/world/cities/discordia/skirts.xml`    | Peri-urban gardens / bushes / clutter                        |
| `public/world/cities/discordia/houses.xml`    | Casas (`Composition`)                                        |
| `public/world/cities/discordia/utilities.xml` | Praça + landmarks (poço, tochas, santuários)                 |
| `public/world/cities/discordia/*.xml`         | Outros distritos: walls, roads, forge, market, …             |
| `public/world/cities/town-demo.xml`           | Isolated demo town (CityGrid prefabs)                        |
| `public/world/vegetation/crystal-vale.xml`    | Biome vegetation / landmarks (deep biomes)                   |
| `public/world/clutter/crystal-vale.xml`       | Extra biome props / debris (StaticSpawner clusters)          |
| `public/world/atmosphere/ambient-fx.xml`      | Ambient particles (`ground-dust`, fireflies, smoke)          |
| `public/world/spawn/ring.xml`                 | Valley resource ring ±58 + peri-urban carpet                 |
| `public/world/creatures/enemies.xml`          | Enemy / boss spawners                                        |
| `public/world/ai/npcs.xml`                    | Quest NPC entities in the Scene                              |
| `public/world/environment.xml`                | Sky, light, post, Weather, `BiomeRegion` (incl. clouds/rain) |

`CityGrid` / `Street` / `Building` / `Slot` recipes: cell coords space-separated (`at="2 1"`). Details in [`public/world/context.md`](public/world/context.md) and engine `src/plugins/city-layout/context.md`.

## Extending

- Add more assets: add rows to a pack manifest in `examples/shared-assets/manifests/` (or create a new pack there), re-run `gameassets resume` + handoff from the pool.
- Change layout: edit the matching file under `public/world/` (not the whole `index.html`), or regenerate via `gameassets dream`.
- Add game logic: edit `src/main.ts` and the entity scripts under `src/scripts/`. Add new systems with `withSystem`.
- Add quests: drop a new JSON into `src/data/quests/`, import it in `src/main.ts`, and add a matching `<DialogueNPC dialogue-id="…">` in `public/world/ai/npcs.xml`.
- Add enemies or bosses: write an entity script in `src/scripts/` (see `creature.ts` for the shared `createCreatureBehaviours` builder) and spawn it via `<DynamicSpawner>` in `public/world/creatures/enemies.xml` or a placed `<GameObject>`.
- Tweak AI presets: edit the YAML under `public/data/ai/` (boss, goblin, slime), loaded into the data registry at boot.
- Add particle effects: presets load sprites from `/assets/particles/`; destruction uses `dust`/`leaves`/`woodchips`/`rockshards`; campfire uses `fire`/`smoke`. Add more via `<ParticleSystem preset="…">` / `<ParticleBurst>`.
- Use `gameassets dream "your idea" --dry-run` to regenerate a full plan + files from scratch.

## Game Design Document

The fiction and design layer on top of this demo lives in
[`docs/gdd/`](docs/gdd/README.md) — _Discordia: A Nota do Mundo_. Written in
**Brazilian Portuguese (PT-BR)** and organized as a fractal: a base document
plus one folder per branch (vision, narrative, world, gameplay, content,
technical, UX, production).

Entries marked **[PROPOSTA]** describe design that is not implemented yet;
everything else documents what already ships in this example.

## Related docs

- [docs/gdd/README.md](docs/gdd/README.md): GDD base (pitch, pillars, fractal index)
- [public/world/context.md](public/world/context.md): modular map fragments + city contracts
- [scripts/README_VEGETATION.md](scripts/README_VEGETATION.md): generate grass/flower GLBs
- Engine: [vegetation](../../src/plugins/vegetation/context.md), [spawner](../../src/plugins/spawner/context.md) (estáticos AABB; creatures CCT), [terrain](../../src/plugins/terrain/context.md) (`TerrainPad`, noise-sand), [spawn-variation](../../src/plugins/spawn-variation/context.md), [loading](../../src/plugins/loading/context.md)
- [MONOREPO_GAME_PIPELINE.md](../../../docs/MONOREPO_GAME_PIPELINE.md): folder layout and handoff contract
- [ZERO_TO_GAME_AI.md](../../../docs/ZERO_TO_GAME_AI.md): AI-centric workflow and the `dream` command
- [GameAssets README](../../../GameAssets/README.md): batch, handoff, presets
- [Plugins overview](../../src/plugins/README.md): engine plugin architecture (`DefaultPlugins`)
- [AUDIO.md](../../docs/AUDIO.md): bank, deferred preload / autoplay gesture, spatial cull, profiler Audio, melee impact timing
- [vite/context.md](../../src/vite/context.md): Typr GPOS noise silence, yoga/uikit `optimizeDeps` exclude
- [VIBEGAME_AUDIO_COMBAT_FINDINGS.md](../../../docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md): agent lessons (SFX length, 0.35 fraction)
- [hello-world example](../hello-world/context.md): minimal Vite scene (no handoff required)
