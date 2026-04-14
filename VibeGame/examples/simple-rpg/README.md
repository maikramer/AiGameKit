# Simple RPG Demo — GameDev monorepo pipeline

End-to-end example of the **GameDev monorepo workflow**: describe assets in `game.yaml` + `manifest.csv`, generate **GLBs** (Text3D + Paint3D), optional **rigging** (Rigging3D), **audio** (Text2Sound), **sky** (Skymap2D), **handoff** to `public/assets/`, and run a playable **VibeGame** scene.

This demo also showcases VibeGame's **new engine features**: particles, AI steering NPCs, save/load, i18n, declarative **sky** (`<Skyboxurl="…">`) and **audio** (`<AudioSource>` + `resume-audio-on-user-gesture`) — plus lightweight gameplay code for HUD and SFX triggers.

**Português:** demo completa do pipeline do monorepo GameDev: GameAssets batch gera GLBs, áudio e imagens; handoff copia para `public/`; VibeGame carrega GLBs via `<GLTFLoader` / `<PlayerGLTF`, céu equirect com `<Skybox>`, e clips com `<AudioSource>`. A API `playAudioEmitter` dispara SFX nomeados; ver [`docs/AUDIO.md`](../../docs/AUDIO.md). Novas features: partículas, NPCs com IA, save/load e i18n.

## What is in the scene

| Element                      | Source / Plugin                        | How it loads                                                   |
| ---------------------------- | -------------------------------------- | -------------------------------------------------------------- |
| Terrain (256m, LOD)          | Built-in `<Terrain>`                   | Declarative in `index.html`                                    |
| Ocean water plane            | Built-in `<Water>`                     | Declarative                                                    |
| Atmospheric fog              | Built-in `<Fog>`                       | Declarative                                                    |
| Player (animated GLB + WASD) | Built-in `<PlayerGLTF`                 | Declarative                                                    |
| Follow camera + post-fx      | Built-in `<FollowCamera>`              | Declarative (bloom, vignette, chromatic aberration)            |
| Hero character (GLB, rigged) | Text3D + Paint3D + Rigging3D           | `<PlayerGLTF model-url="...">`                                 |
| Stone pillar                 | Text3D + Paint3D                       | **`<GameObject place="at: x z">`** (terrain height + AABB)     |
| Wooden crates (x2)           | Text3D + Paint3D                       | **`<GameObject place="at: x z">`**                             |
| Blue crystals (x2)           | Text3D + Paint3D                       | **`<GameObject place="at: x z; base-y-offset: …">`**           |
| Lowpoly trees (x24 spawned)  | Text3D + Paint3D + Spawner             | `<SpawnGroup profile="tree">`                                  |
| Physics crates (x6 spawned)  | Spawner + Physics                      | `<SpawnGroup profile="physics-box">`                           |
| GLB pushable crates (x3)     | Spawner + Physics                      | `<SpawnGroup profile="gltf-crate">`                            |
| Campfire (fire + smoke)      | **Particles + Spawner**                | `<GameObject place="at: x z; y-offset: …">` + `ParticleSystem` |
| Crystal sparkles (x2)        | **Particles + Spawner**                | `<GameObject place="at: x z; …">` + `ParticleSystem`           |
| Ambient rain                 | **Particles plugin**                   | `<ParticleSystem preset="rain">` (high Y)                      |
| Wandering NPCs (x3)          | **AI Steering + Spawner**              | `<GameObject place="at: x z; align-to-terrain: 0; …"><NPC>`    |
| Save / Load                  | **Save-Load plugin**                   | `withPlugin(SaveLoadPlugin)` in `src/main.ts`                  |
| Localized messages (EN/PT)   | **i18n plugin**                        | `withPlugin(I18nPlugin)` + `loadDictionary`                    |
| On-screen status overlay     | Custom DOM via gameplay system         | `withSystem(GameplayHudSystem)` in `src/main.ts`               |
| Sky IBL + background         | Skymap2D (equirect PNG) + `sky` plugin | **`<Skyboxurl="/assets/sky/sky.png">`** em `index.html`        |
| BGM + SFX (jump, save, load) | Text2Sound + `audio` plugin            | **`<AudioSource>`** + `playAudioEmitter` em `src/main.ts`      |

## Engine features demonstrated

| Feature                  | Plugin             | Usage in this demo                                                                     |
| ------------------------ | ------------------ | -------------------------------------------------------------------------------------- |
| Particles                | `ParticlesPlugin`  | Fire, smoke, sparks, rain (often under `<GameObject place="…">` for ground height)     |
| `<GameObject place="…">` | `SpawnerPlugin`    | Deterministic XZ + terrain Y on the root entity; children are local transforms / merge |
| AI Steering              | `AiSteeringPlugin` | 3 NPCs wandering autonomously (Yuka)                                                   |
| Save / Load              | `SaveLoadPlugin`   | Q = save, E = load via localStorage + msgpackr                                         |
| i18n                     | `I18nPlugin`       | Auto-detect PT/EN; overlay messages localized                                          |
| Audio                    | `AudioPlugin`      | `<AudioSource>` + `resume-audio-on-user-gesture`; SFX por nome                         |
| Raycast                  | `RaycastPlugin`    | Available (not used directly in this demo yet)                                         |
| Joints                   | `JointsPlugin`     | Available (not used directly in this demo yet)                                         |
| Navmesh                  | `NavmeshPlugin`    | Available (not used directly in this demo yet)                                         |

## Pipeline (step by step)

### 1. Review the plan

The scene layout and assets were generated by **`gameassets dream`** (dry-run, no GPU):

```
sample-gameassets/
  dream_plan.json   # LLM-generated plan (or fallback)
  game.yaml         # GameAssets batch profile (output_dir → ../public/assets/)
  manifest.csv      # Asset list (ids, prompts, flags)
  world.xml         # VibeGame scene (for reference)
  main.ts           # Bootstrap code (for reference)
  index.html        # Full page (for reference)
# GLB/PNG/WAV gerados pelo batch: `public/assets/{meshes,images,audio}/` (local; não versionados no Git).
# No repositório mantêm-se só `public/assets/{models,textures,audio,sky,terrain}/`.
```

### 2. Generate assets (requires GPU)

From the `sample-gameassets/` directory:

```bash
cd VibeGame/examples/simple-rpg/sample-gameassets

# 2D images + 3D meshes + PBR textures + rigging
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d --with-rig

# Sky (separate CLI) — escrever directamente em public/assets/sky/
skymap2d generate "bright blue sky with soft clouds over green plains, equirectangular 360" -o ../public/assets/sky/sky.png
```

### 3. Handoff into public/

```bash
gameassets handoff \
  --profile game.yaml \
  --manifest manifest.csv \
  --public-dir ../public \
  --with-textures

# Se usaste -o sky.png na pasta antiga, move: mkdir -p ../public/assets/sky && mv sky.png ../public/assets/sky/
```

This creates (or refreshes):

```
public/
  assets/
    meshes/     # saída intermédia do batch (GLB por id) — só local, .gitignore
    images/     # PNG 2D do Text2D — só local, .gitignore
    models/     # GLB servidos pelo Vite (handoff)
    textures/   # PNG difusos (handoff)
    audio/      # WAV do Text2Sound
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
- Change layout: edit `index.html` (`<GameObject place="…">` wrappers, `<GLTFLoader`, etc.) or regenerate via `gameassets dream`.
- Add game logic: edit `src/main.ts` using the VibeGame runtime API and custom systems.
- Add more particle effects: `<ParticleSystem preset="snow">`, `<ParticleBurst preset="explosion">`.
- Add pathfinding: `<NavMeshSurface>` + `<NavMeshAgent target="x y z">` for AI navigation.
- Add physics joints: `<Joint joint-type="revolute">` for connected objects.
- Use `gameassets dream "your idea" --dry-run` to regenerate the full plan + files.

## Related docs

- [MONOREPO_GAME_PIPELINE.md](../../../docs/MONOREPO_GAME_PIPELINE.md) — folder layout and handoff contract
- [ZERO_TO_GAME_AI.md](../../../docs/ZERO_TO_GAME_AI.md) — AI-centric workflow and `dream` command
- [GameAssets README](../../../GameAssets/README.md) — batch, handoff, presets
- [Plugins overview](../../src/plugins/README.md) — engine plugin architecture (`DefaultPlugins`)
- [AUDIO.md](../../docs/AUDIO.md) — Howler, `<AudioSource>`, autoplay no browser
- [hello-world example](../hello-world/context.md) — minimal Vite scene (`<GameObject place="…">`, no handoff required)
