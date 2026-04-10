# Simple RPG Demo — GameDev monorepo pipeline

End-to-end example of the **GameDev monorepo workflow**: describe assets in `game.yaml` + `manifest.csv`, generate **GLBs** (Text3D + Paint3D), optional **rigging** (Rigging3D), **audio** (Text2Sound), **sky** (Skymap2D), **handoff** to `public/assets/`, and run a playable **VibeGame** scene.

This demo also showcases VibeGame's **new engine features**: particles, AI steering NPCs, save/load, i18n, declarative **sky** (`<sky url="…">`) and **audio** (`<audio-clip>` + `resume-audio-on-user-gesture`) — plus lightweight gameplay code for HUD and SFX triggers.

**Português:** demo completa do pipeline do monorepo GameDev: GameAssets batch gera GLBs, áudio e imagens; handoff copia para `public/`; VibeGame carrega GLBs via `<gltf-load>` / `<player-gltf>`, céu equirect com `<sky>`, e clips com `<audio-clip>`. A API `playAudioEmitter` dispara SFX nomeados; ver [`docs/AUDIO.md`](../../docs/AUDIO.md). Novas features: partículas, NPCs com IA, save/load e i18n.

## What is in the scene

| Element                      | Source / Plugin                        | How it loads                                             |
| ---------------------------- | -------------------------------------- | -------------------------------------------------------- |
| Terrain (256m, LOD)          | Built-in `<terrain>`                   | Declarative in `index.html`                              |
| Ocean water plane            | Built-in `<water>`                     | Declarative                                              |
| Atmospheric fog              | Built-in `<fog>`                       | Declarative                                              |
| Player (animated GLB + WASD) | Built-in `<player-gltf>`               | Declarative                                              |
| Follow camera + post-fx      | Built-in `<follow-camera>`             | Declarative (bloom, vignette, chromatic aberration)      |
| Hero character (GLB, rigged) | Text3D + Paint3D + Rigging3D           | `<player-gltf model-url="...">`                          |
| Stone pillar                 | Text3D + Paint3D                       | **`<place at="x z">`** (terrain height + AABB)           |
| Wooden crates (x2)           | Text3D + Paint3D                       | **`<place at="x z">`**                                   |
| Blue crystals (x2)           | Text3D + Paint3D                       | **`<place at="x z" y-offset="0.6">`**                    |
| Lowpoly trees (x24 spawned)  | Text3D + Paint3D + Spawner             | `<spawn-group profile="tree">`                           |
| Physics crates (x6 spawned)  | Spawner + Physics                      | `<spawn-group profile="physics-box">`                    |
| GLB pushable crates (x3)     | Spawner + Physics                      | `<spawn-group profile="gltf-crate">`                     |
| Campfire (fire + smoke)      | **Particles + Spawner**                | `<place at="x z" y-offset="…">` + `particle-emitter`     |
| Crystal sparkles (x2)        | **Particles + Spawner**                | `<place at="x z" y-offset="…">` + `particle-emitter`     |
| Ambient rain                 | **Particles plugin**                   | `<particle-emitter preset="rain">` (high Y)              |
| Wandering NPCs (x3)          | **AI Steering + Spawner**              | `<place align-to-terrain="0">` + `<npc>`                 |
| Save / Load                  | **Save-Load plugin**                   | `withPlugin(SaveLoadPlugin)` in `src/main.ts`            |
| Localized messages (EN/PT)   | **i18n plugin**                        | `withPlugin(I18nPlugin)` + `loadDictionary`              |
| On-screen status overlay     | Custom DOM via gameplay system         | `withSystem(GameplayHudSystem)` in `src/main.ts`         |
| Sky IBL + background         | Skymap2D (equirect PNG) + `sky` plugin | **`<sky url="/assets/sky/sky.png">`** em `index.html`    |
| BGM + SFX (jump, save, load) | Text2Sound + `audio` plugin            | **`<audio-clip>`** + `playAudioEmitter` em `src/main.ts` |

## Engine features demonstrated

| Feature     | Plugin             | Usage in this demo                                            |
| ----------- | ------------------ | ------------------------------------------------------------- |
| Particles   | `ParticlesPlugin`  | Fire, smoke, sparks, rain (via `<place>` for ground height)   |
| `<place>`   | `SpawnerPlugin`    | Deterministic XZ + terrain Y + optional AABB align            |
| AI Steering | `AiSteeringPlugin` | 3 NPCs wandering autonomously (Yuka)                          |
| Save / Load | `SaveLoadPlugin`   | Q = save, E = load via localStorage + msgpackr                |
| i18n        | `I18nPlugin`       | Auto-detect PT/EN; overlay messages localized                 |
| Audio       | `AudioPlugin`      | `<audio-clip>` + `resume-audio-on-user-gesture`; SFX por nome |
| Raycast     | `RaycastPlugin`    | Available (not used directly in this demo yet)                |
| Joints      | `JointsPlugin`     | Available (not used directly in this demo yet)                |
| Navmesh     | `NavmeshPlugin`    | Available (not used directly in this demo yet)                |

## Pipeline (step by step)

### 1. Review the plan

The scene layout and assets were generated by **`gameassets dream`** (dry-run, no GPU):

```
sample-gameassets/
  dream_plan.json   # LLM-generated plan (or fallback)
  game.yaml         # GameAssets batch profile
  manifest.csv      # Asset list (ids, prompts, flags)
  world.xml         # VibeGame scene (for reference)
  main.ts           # Bootstrap code (for reference)
  index.html        # Full page (for reference)
```

### 2. Generate assets (requires GPU)

From the `sample-gameassets/` directory:

```bash
cd VibeGame/examples/simple-rpg/sample-gameassets

# 2D images + 3D meshes + PBR textures + rigging
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d --with-rig

# Sky (separate CLI)
skymap2d generate "bright blue sky with soft clouds over green plains, equirectangular 360" -o sky.png
```

### 3. Handoff into public/

```bash
gameassets handoff \
  --profile game.yaml \
  --manifest manifest.csv \
  --public-dir ../public \
  --with-textures

# Copy sky manually (not part of handoff yet)
mkdir -p ../public/assets/sky
cp sky.png ../public/assets/sky/sky.png
```

This creates:

```
public/
  assets/
    models/hero.glb, wooden_crate.glb, crystal_blue.glb, stone_pillar.glb, tree_lowpoly.glb
    audio/bgm_field.wav, sfx_jump.wav, sfx_save.wav, sfx_load.wav
    textures/hero.png, ...  (optional diffuse images)
    sky/sky.png
    gameassets_handoff.json
```

### 4. Run the game

```bash
cd VibeGame/examples/simple-rpg
bun install   # first time only
bun run dev   # http://localhost:3011
```

### Without GPU (just the engine)

The scene still runs without GLBs — you see the terrain, the player capsule, particles, wandering NPCs, and HUD panels. Missing GLBs log warnings to the console.

## Controls

| Input            | Action                    |
| ---------------- | ------------------------- |
| W A S D          | Move (relative to camera) |
| Space            | Jump                      |
| Q                | Save game (localStorage)  |
| E                | Load game (localStorage)  |
| Right mouse drag | Orbit camera              |
| Mouse wheel      | Zoom                      |

## Extending

- Add more assets: edit `manifest.csv`, re-run batch + handoff.
- Change layout: edit `index.html` `<gltf-load>` positions, or regenerate via `gameassets dream`.
- Add game logic: edit `src/main.ts` using the VibeGame runtime API and custom systems.
- Add more particle effects: `<particle-emitter preset="snow">`, `<particle-burst preset="explosion">`.
- Add pathfinding: `<nav-mesh>` + `<nav-agent target="x y z">` for AI navigation.
- Add physics joints: `<joint joint-type="revolute">` for connected objects.
- Use `gameassets dream "your idea" --dry-run` to regenerate the full plan + files.

## Related docs

- [MONOREPO_GAME_PIPELINE.md](../../../docs/MONOREPO_GAME_PIPELINE.md) — folder layout and handoff contract
- [ZERO_TO_GAME_AI.md](../../../docs/ZERO_TO_GAME_AI.md) — AI-centric workflow and `dream` command
- [GameAssets README](../../../GameAssets/README.md) — batch, handoff, presets
- [PLUGINS.md](../../docs/PLUGINS.md) — full engine plugin reference
- [AUDIO.md](../../docs/AUDIO.md) — Howler, `<audio-clip>`, autoplay no browser
- [monorepo-game example](../monorepo-game/) — minimal GLB bridge (imperative `loadGltfToScene`)
