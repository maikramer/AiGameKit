# Simple RPG Demo: Crystal Vale (GameDev monorepo pipeline)

End-to-end example of the **GameDev monorepo workflow**: describe assets in `game.yaml` + `manifest_full.csv`, generate **GLBs** (Text3D + Paint3D), optional **rigging** (Rigging3D) and **animation** (Animator3D), **audio** (Text2Sound), **sky** (Skymap2D), **handoff** to `public/assets/`, and run a playable **VibeGame** scene.

The demo grew past a bare scene into a small but complete action RPG. The world is a central walled city surrounded by **four biome regions** (dark forest, desert, swamp, frozen peaks), each with its own enemies, a boss, and three quest NPCs. That is **12 quests** and **4 bosses** total, plus an XP and skill progression system, a gold economy with a merchant, bombs, consumables, and abilities. Save and load live in the pause menu's Options tab.

On the engine side it exercises the full RPG plugin stack: combat, inventory, progression, economy, status effects, melee-AI (a shared FSM that drives every creature and boss), pause coordination, spawn gating, navmesh, save/load, and i18n. Declarative **sky** (`<EquirectSky url="…">`) and **audio** (`<AudioMixer>` + `<MusicLayer>`, `defineSoundBank` + `playSound`, with `resume-audio-on-user-gesture`) round it out.

**Português:** demo completa do pipeline do monorepo GameDev. O GameAssets batch gera GLBs, áudio e imagens; o handoff copia para `public/`; o VibeGame carrega GLBs via `<GLTFLoader>` / `<PlayerGLTF>`, céu equirect com `<EquirectSky>`, e SFX nomeados via `defineSoundBank` / `playSound` (ver [`docs/AUDIO.md`](../../docs/AUDIO.md)). O demo é um RPG completo: **4 biomas** (floresta sombria, deserto, pântano, picos gelados), **12 quests** (3 por bioma), **4 chefes**, XP e árvore de habilidades, economia de ouro com comerciante, bombas, consumíveis e habilidades. Save/load ficam no menu de pausa (aba Opções). Stack de plugins: combate, inventário, progressão, economia, efeitos de status, IA melee, pausa, gating de spawn, navmesh, save/load e i18n (EN/PT).

## Getting started

The 3D assets (GLB meshes, textures, terrain, sky, audio) are large binary
blobs, so they are **not committed to git**. They live in a pinned GitHub
Release and are fetched on demand:

```bash
npm install        # or: bun install
npm run dev        # predev runs scripts/fetch-assets.mjs automatically
```

`scripts/fetch-assets.mjs` downloads the bundle pinned in `assets.lock.json`,
verifies its sha256, and extracts it into `public/assets/` (idempotent: it
no-ops once the pinned version is present). Run it directly with `npm run setup`
if needed. To bump the assets, regenerate them with the GameAssets pipeline,
upload a new release, and update `assets.lock.json` (`version` + `url` + `sha256`).

## What is in the scene

| Element                                    | Source / Plugin                        | How it loads                                                                                                               |
| ------------------------------------------ | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Terrain (2 km, quadtree LOD)               | Built-in `<Terrain>`                   | Declarative in `index.html` (matches `public/assets/terrain/heightmap.png`)                                                |
| Sky IBL + background                       | Skymap2D (equirect PNG) + `sky` plugin | `<EquirectSky url="/assets/sky/sky.png">` in `index.html`                                                                  |
| NavMesh                                    | `NavMeshPlugin`                        | `<NavMesh>` in `index.html` (AI pathfinding surface)                                                                       |
| Player (animated GLB + WASD)               | Built-in `<PlayerGLTF>`                | `<PlayerGLTF name="hero" model-url="/assets/meshes/hero_rigged_animated.glb">`                                             |
| Third-person camera + post-fx              | Built-in `<ThirdPersonCamera>`         | Declarative (bloom, vignette, SSAO, AGX tonemap; `<PostFxDebugToggle>` cycles effects on 1-6)                              |
| Audio mixer + layered music                | Engine audio plugin                    | `<AudioMixer>` + `<MusicLayer layer="explore\|battle">` (crossfaded by biome)                                              |
| Central walled city                        | Built-in `<Composition>` + primitives  | Walls with 4 cardinal gates, corner towers, 4 houses, well, campfire, market stalls, torches                               |
| Spawn exclusion (city)                     | `spawner` plugin                       | `<SpawnExclusion at="0 0" radius="30">` keeps resources/enemies out of the city                                            |
| City merchant                              | Entity script                          | `<GameObject name="merchant" script="merchant.ts">` (press **K** to trade)                                                 |
| Interactables (rune pillar, shrine, chest) | Entity scripts + trimesh colliders     | `<GameObject script="…">` (press **F** to interact)                                                                        |
| Static resources (trees, rocks, cacti)     | `<StaticSpawner>` + `<ResourceNode>`   | Per-biome: oak, pine_dark, dead_willow, cactus, ruin_pillar, etc. (chop/mine with **J**)                                   |
| Biome enemies (animated)                   | `<DynamicSpawner>` + entity scripts    | wolf, shade, scorpion, bandit, bogling, mosquito, goblin, slime                                                            |
| Biome regions (fog/ambient/BGM)            | `biomes` plugin                        | `<BiomeRegion polygon="[x,z;…]">` x4 (dark forest, desert, swamp, frozen peaks)                                            |
| Quest NPCs + dialogue (12)                 | `quests` plugin                        | `<DialogueNPC>` inside `<Composition>` + `<DialogueBalloon>`                                                               |
| Bosses (4)                                 | Entity scripts                         | `<GameObject script="bosses/*.ts">` (witch, sand-wyrm, bog-warden) + `<GameObject script="boss.ts">` (ogre, final)         |
| HUD widgets                                | `hud` plugin                           | `<HealthBar>`, `<XpBar>`, `<ResourceChip>` (gold/wood/stone), `<Minimap>`, `<Compass>`, `<BossBar>`, `<InteractionPrompt>` |
| Pause menu (tabbed modal)                  | `hud` plugin                           | `<TabbedModal key="q">` with Skills, Inventory, Options, Quests tabs                                                       |
| Particles                                  | Engine particle system                 | Destruction bursts (dust/explosion presets) on resource nodes                                                              |
| Save / Load                                | `SaveLoadPlugin`                       | Buttons in the pause menu **Options** tab (localStorage + msgpackr)                                                        |
| Localized messages (EN/PT)                 | `i18n` plugin                          | `loadDictionary` + auto-detected locale                                                                                    |

## Engine features demonstrated

| Feature            | Plugin                   | Usage in this demo                                                                                      |
| ------------------ | ------------------------ | ------------------------------------------------------------------------------------------------------- |
| Combat             | `CombatPlugin`           | Melee swings (sword/axe/spear), thrown bombs, enemy hit feedback, damage numbers                        |
| Inventory          | `InventoryPlugin`        | Stackable items (wood, stone, bomb, potions, quest rewards); `<InventoryTab>` in pause menu             |
| Progression        | `ProgressionPlugin`      | XP on kill, level up, stat modifiers (Vitality, Strength, Agility); `<SkillsTab>` in pause menu         |
| Economy            | `EconomyPlugin`          | Gold counter, merchant buy/sell (ring speed upgrade, sword damage upgrade)                              |
| Status effects     | `StatusEffectsPlugin`    | Poison, buffs (consumables and abilities apply them)                                                    |
| RPG melee AI       | `RpgAiPlugin`            | One engine FSM (`runMeleeAiFrame`) drives every creature and boss: detect, chase, lunge, strafe, enrage |
| Pause coordination | `PauseCoordinatorPlugin` | Freezes simulation while the pause modal is open                                                        |
| Spawn gating       | `SpawnGatePlugin`        | Final boss stays dormant until its gate condition clears                                                |
| NavMesh            | `NavMeshPlugin`          | Pathfinding surface for chasing AI                                                                      |
| Save / Load        | `SaveLoadPlugin`         | Options tab Save/Load buttons (localStorage + msgpackr); merchant progress serializer                   |
| i18n               | `I18nPlugin`             | Auto-detect PT/EN; HUD, modal, and controls text localized                                              |
| Audio              | Engine audio             | `defineSoundBank` + `playSound`, `<AudioMixer>` buses, layered `<MusicLayer>` with biome crossfade      |
| Spawners           | `spawner` plugin         | `<StaticSpawner>` (terrain-aligned resources) and `<DynamicSpawner>` (enemies) with deterministic seeds |
| Particles          | Engine particles         | Dust/explosion bursts when destructible nodes break                                                     |
| Terrain            | `terrain` plugin         | Heightmap with quadtree LOD and per-chunk Rapier heightfield collision                                  |
| Biome detection    | `biomes` plugin          | Fog color/density, ambient light, terrain texture, and BGM layer crossfade by `<BiomeRegion>`           |

> Note: this demo does **not** use `AiSteeringPlugin` (Yuka wandering). All
> creatures and bosses run through the engine melee-AI FSM from `RpgAiPlugin`.

## Pipeline (step by step)

### 1. Review the plan

The scene layout and assets were planned with **`gameassets dream`** (dry-run, no GPU) and then refined per biome. The source manifests live in `sample-gameassets/`:

```
sample-gameassets/
  game.yaml                      # GameAssets batch profile (output_dir → ../public/assets/)
  manifest_full.csv              # Full asset list (ids, prompts, flags)
  manifest.yaml                  # Subset manifest (batch input)
  manifest.boss_ogre.yaml        # Final boss (ogre) subset manifest
  manifest.dark_forest.yaml      # Dark forest biome subset
  manifest.desert.yaml           # Desert biome subset
  manifest.swamp.yaml            # Swamp biome subset
  presets-local.yaml             # Local quality/preset overrides
  run-batch-*.sh                 # Per-stage convenience scripts (3d, audio, biomes)
# GLB/PNG/WAV generated by batch land in public/assets/{meshes,images,audio,sky,terrain,textures}/
# (local only, gitignored). Only small JSON metadata is committed.
```

### 2. Generate assets (requires GPU)

From the `sample-gameassets/` directory:

```bash
cd VibeGame/examples/simple-rpg/sample-gameassets

# 2D images + 3D meshes + PBR textures + rigging + animation
gameassets batch --profile game.yaml --manifest manifest_full.csv

# Sky (separate CLI): write directly into public/assets/sky/
skymap2d generate "bright blue sky with soft clouds over green plains, equirectangular 360" -o ../public/assets/sky/sky.png
```

### 3. Handoff into public/

```bash
gameassets handoff \
  --profile game.yaml \
  --manifest manifest_full.csv \
  --public-dir ../public \
  --with-textures
```

This creates (or refreshes):

```
public/
  assets/
    meshes/     # final GLBs (lod0/lod1/lod2, collision, rigged+animated): local, gitignored
    images/     # Text2D PNGs: local, gitignored
    textures/   # diffuse/PBR textures used by terrain and biomes
    audio/      # Text2Sound WAV/OGG
    sky/sky.png
    terrain/    # heightmap.png + terrain.json
    gameassets_handoff.json
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

| Input            | Action                                             |
| ---------------- | -------------------------------------------------- |
| W A S D          | Move (relative to camera)                          |
| Shift            | Sprint                                             |
| Space            | Jump                                               |
| J                | Attack / harvest (primary action; swing weapon)    |
| F                | Interact (NPCs, chests, shrines, readables)        |
| K                | Trade with the merchant                            |
| B                | Bomb (tap to drop, hold to aim and lob)            |
| V                | Cycle held weapon (sword / axe / spear)            |
| 1                | Use potion (heal)                                  |
| 2                | Use antidote (cure poison)                         |
| C                | Dash                                               |
| E                | Heal (ability)                                     |
| R                | Power Strike (ability)                             |
| Q                | Pause menu (Skills / Inventory / Options / Quests) |
| Right mouse drag | Orbit camera                                       |
| Mouse wheel      | Zoom                                               |

Save and load are **not** bound to keys anymore. They live as buttons in the
pause menu's **Options** tab (open with **Q**).

## Biomes and quests

The world spans **four biome regions** radiating from the central walled city. Each cardinal gate leads into a distinct biome with its own atmosphere, enemy types, quest NPCs, and a boss at its far end.

| Biome                   | Location | Atmosphere                 | Enemies                   | Boss         | Quest NPCs (dialogue-id)                       |
| ----------------------- | -------- | -------------------------- | ------------------------- | ------------ | ---------------------------------------------- |
| **Dark Forest** (north) | z > 28   | Green-dark fog, mysterious | wolf, shade               | Witch        | forest_wolves, forest_shades, forest_darkwood  |
| **Desert** (east)       | x > 28   | Sandy fog, arid            | scorpion, bandit          | Sand Wyrm    | desert_scorpions, desert_bandits, desert_ruins |
| **Swamp** (south)       | z < -28  | Murky fog, dense           | bogling, mosquito         | Bog Warden   | swamp_boglings, swamp_bogwarden, swamp_bogmoss |
| **Frozen Peaks** (west) | x < -28  | Cold fog, icy              | goblin, slime, frost wolf | Ogre (final) | peaks_goblins, peaks_frost, peaks_ogre         |

Each biome is declared via `<BiomeRegion polygon="[x,z;x,z;...]">` in `index.html`. The `biomes` plugin detects the player's position and crossfades fog color, density, ambient light, terrain texture, and the BGM layer when entering a new region.

**Quest system:** 12 NPCs (3 per biome) offer quests loaded from `src/data/quests/*.json`. Quests are either kill-N-enemies or collect-N-resources. Walk up to an NPC and press **F** to open the dialogue balloon, accept the quest, then track progress in the **Quests** tab of the pause menu. Quest state persists via `SaveLoadPlugin`.

## Bosses

Four bosses guard the far end of each biome. The first three (Witch, Sand Wyrm, Bog Warden) are placed via `<GameObject script="bosses/*.ts">` and spawn active. They share the same engine melee-AI FSM as regular creatures but with higher HP, wider detect range, strafing, and an enrage phase at low health.

| Boss       | Biome        | Script                 | Notes                                             |
| ---------- | ------------ | ---------------------- | ------------------------------------------------- |
| Witch      | Dark Forest  | `bosses/witch.ts`      | Elite, back of the forest                         |
| Sand Wyrm  | Desert       | `bosses/sand-wyrm.ts`  | Elite, deep desert                                |
| Bog Warden | Swamp        | `bosses/bog-warden.ts` | Elite, far swamp                                  |
| Ogre       | Frozen Peaks | `boss.ts`              | **Final boss**, gated until the peaks are cleared |

The final boss (Ogre) is the `<BossBar>` target in the HUD. It stays dormant until the frozen-peaks biome is cleared, then activates with an intro roar, relentless pursuit (huge detect range, never leashes), strafing, and an enrage phase below 30% HP. Defeating it ends the run with a "BOSS DEFEATED!" message and a gold drop.

## Progression, economy, and abilities

**XP and levels.** Killing enemies awards XP (`addXp` in `CombatFeedbackSystem`). Leveling up grants stat points you can spend in the **Skills** tab (pause menu) on three lines: Vitality (max HP), Strength (attack damage), and Agility (move speed). The `HeroStatsSystem` resolves all active modifiers every frame.

**Gold economy.** Enemies drop gold on death. Spend it at the city merchant (**K**): buy a speed ring (permanent move-speed multiplier) and sword upgrades (flat damage bonus per level, folded into bomb damage too). The merchant progress serializer persists `ringOwned` and `swordLevel` so a reload can't re-grant the ring or reset upgrades.

**Bombs and abilities.** Buy bombs from the merchant, then throw them with **B** (tap to drop at your feet, hold to aim an arc at the nearest enemy). Abilities live on a cooldown bar: **C** dash, **E** heal, **R** power strike (`src/game/abilities.ts`).

**Consumables.** Potions (**1**, heal) and antidotes (**2**, cure poison) sit on a hotbar (`src/game/consumables.ts`). Quest rewards and harvested resources (wood, stone, quest items) stack in the inventory.

## Extending

- Add more assets: edit `sample-gameassets/manifest_full.csv` (and the per-biome subset manifests), re-run batch + handoff.
- Change layout: edit `index.html` (`<Composition>`, `<StaticSpawner>`, `<DynamicSpawner>`, `<BiomeRegion>`, etc.) or regenerate via `gameassets dream`.
- Add game logic: edit `src/main.ts` and the entity scripts under `src/scripts/`. Add new systems with `withSystem`.
- Add quests: drop a new JSON into `src/data/quests/`, import it in `src/main.ts`, and add a matching `<DialogueNPC dialogue-id="…">` in `index.html`.
- Add enemies or bosses: write an entity script in `src/scripts/` (see `creature.ts` for the shared `createCreatureBehaviours` builder) and spawn it via `<DynamicSpawner>` or a placed `<GameObject>`.
- Tweak AI presets: edit the YAML under `public/data/ai/` (boss, goblin, slime), loaded into the data registry at boot.
- Add particle effects: destruction presets (`dust`, `explosion`) are wired to resource nodes; add more via `<ParticleSystem preset="…">` / `<ParticleBurst>`.
- Use `gameassets dream "your idea" --dry-run` to regenerate a full plan + files from scratch.

## Related docs

- [MONOREPO_GAME_PIPELINE.md](../../../docs/MONOREPO_GAME_PIPELINE.md): folder layout and handoff contract
- [ZERO_TO_GAME_AI.md](../../../docs/ZERO_TO_GAME_AI.md): AI-centric workflow and the `dream` command
- [GameAssets README](../../../GameAssets/README.md): batch, handoff, presets
- [Plugins overview](../../src/plugins/README.md): engine plugin architecture (`DefaultPlugins`)
- [AUDIO.md](../../docs/AUDIO.md): Howler, `<AudioSource>`, autoplay in the browser
- [hello-world example](../hello-world/context.md): minimal Vite scene (no handoff required)
