# Simple RPG Demo — GameDev monorepo pipeline

End-to-end example of the **GameDev monorepo workflow**: describe assets in `game.yaml` + `manifest_full.csv`, generate **GLBs** (Text3D + Paint3D), optional **rigging** (Rigging3D), **audio** (Text2Sound), **sky** (Skymap2D), **handoff** to `public/assets/`, and run a playable **VibeGame** scene.

This demo also showcases VibeGame's **new engine features**: particles, AI steering NPCs, save/load, i18n, declarative **sky** (`<EquirectSky url="…">`) and **audio** (`defineSoundBank` + `playSound`, with `resume-audio-on-user-gesture`) — plus lightweight gameplay code for HUD and SFX triggers.

**Português:** demo completa do pipeline do monorepo GameDev: GameAssets batch gera GLBs, áudio e imagens; handoff copia para `public/`; VibeGame carrega GLBs via `<GLTFLoader` / `<PlayerGLTF`, céu equirect com `<EquirectSky>`, e SFX nomeados via `defineSoundBank` / `playSound` (ver [`docs/AUDIO.md`](../../docs/AUDIO.md)). Novas features: partículas, NPCs com IA, save/load e i18n.

## Getting started

The 3D assets (GLB meshes, textures, terrain, sky, audio) are large binary
blobs, so they are **not committed to git** — they live in a pinned GitHub
Release and are fetched on demand:

```bash
npm install        # or: bun install
npm run dev        # predev runs scripts/fetch-assets.mjs automatically
```

`scripts/fetch-assets.mjs` downloads the bundle pinned in `assets.lock.json`,
verifies its sha256, and extracts it into `public/assets/` (idempotent — it
no-ops once present). Run it directly with `npm run setup` if needed. To bump
the assets, regenerate them with the GameAssets pipeline, upload a new release,
and update `assets.lock.json` (`version` + `url` + `sha256`).

## What is in the scene

| Element                      | Source / Plugin                        | How it loads                                                                    |
| ---------------------------- | -------------------------------------- | ------------------------------------------------------------------------------- |
| Terrain (10 km, LOD)         | Built-in `<Terrain>`                   | Declarative in `index.html` (matches `public/assets/terrain/terrain.json`)      |
| Ocean water plane            | Built-in `<Water>`                     | _Disabled for performance_ (not in `index.html`)                                |
| Atmospheric fog              | Built-in `<Fog>`                       | _Disabled for performance_ (not in `index.html`)                                |
| Player (animated GLB + WASD) | Built-in `<PlayerGLTF>`                | Declarative                                                                     |
| Follow camera + post-fx      | Built-in `<ThirdPersonCamera>`         | Declarative (bloom, vignette, SSAO, AGX tonemap; CA off)                        |
| Hero character (GLB)         | Player asset                           | `<PlayerGLTF model-url="/assets/meshes/hero_rigged_animated.glb">`              |
| Stone pillars                | Spawner + terrain align                | _Disabled for performance_ (the rune-pillar landmark still uses `stone_pillar`) |
| Lowpoly trees (densidade)    | Spawner                                | `tree_oak_lod0.glb`; `density-per-km2`, escala 1.5–4×, yaw em passos de 45°     |
| Pushable wooden crates       | Spawner + Physics                      | _Disabled for performance_ (`wooden_crate_lod0.glb` exists but isn't spawned)   |
| Save / Load                  | **Save-Load plugin**                   | `withPlugin(SaveLoadPlugin)` in `src/main.ts`                                   |
| Localized messages (EN/PT)   | **i18n plugin**                        | `withPlugin(I18nPlugin)` + `loadDictionary`                                     |
| On-screen status overlay     | Custom DOM via gameplay system         | `withSystem(GameplayHudSystem)` in `src/main.ts`                                |
| Sky IBL + background         | Skymap2D (equirect PNG) + `sky` plugin | **`<EquirectSky url="/assets/sky/sky.png">`** em `index.html`                   |
| BGM + SFX (save, load, …)    | Text2Sound + `audio` plugin            | **`defineSoundBank`** + **`playSound`** em `src/game/sounds.ts`                 |

## Engine features demonstrated

| Feature                  | Plugin             | Usage in this demo                                                                     |
| ------------------------ | ------------------ | -------------------------------------------------------------------------------------- |
| Particles                | `ParticlesPlugin`  | Fire, smoke, sparks, rain (often under `<GameObject place="…">` for ground height)     |
| `<GameObject place="…">` | `SpawnerPlugin`    | Deterministic XZ + terrain Y on the root entity; children are local transforms / merge |
| AI Steering              | `AiSteeringPlugin` | 3 NPCs wandering autonomously (Yuka)                                                   |
| Save / Load              | `SaveLoadPlugin`   | G = save, H = load via localStorage + msgpackr                                         |
| i18n                     | `I18nPlugin`       | Auto-detect PT/EN; overlay messages localized                                          |
| Audio                    | `AudioPlugin`      | `defineSoundBank` + `playSound`; `resume-audio-on-user-gesture`                     |
| Raycast                  | `RaycastPlugin`    | Available (not used directly in this demo yet)                                         |
| Joints                   | `JointsPlugin`     | Available (not used directly in this demo yet)                                         |
| Navmesh                  | `NavmeshPlugin`    | Available (not used directly in this demo yet)                                         |

## Pipeline (step by step)

### 1. Review the plan

The scene layout and assets were generated by **`gameassets dream`** (dry-run, no GPU):

```
sample-gameassets/
  dream_plan.json          # LLM-generated plan (or fallback)
  game.yaml                # GameAssets batch profile (output_dir → ../public/assets/)
  manifest_full.csv        # Asset list (ids, prompts, flags) — the full asset set
  manifest.yaml            # Subset manifest (batch input)
  manifest.boss_ogre.yaml  # Boss ogre subset manifest
  world.xml                # VibeGame scene (for reference)
  main.ts                  # Bootstrap code (for reference)
  index.html               # Full page (for reference)
# GLB/PNG/WAV gerados pelo batch: `public/assets/{meshes,images,audio}/` (local; não versionados no Git).
# No repositório mantêm-se só `public/assets/{audio,sky,terrain}/` (+ JSON dos WAV onde aplicável).
```

### 2. Generate assets (requires GPU)

From the `sample-gameassets/` directory:

```bash
cd VibeGame/examples/simple-rpg/sample-gameassets

# 2D images + 3D meshes + PBR textures + rigging
gameassets batch --profile game.yaml --manifest manifest_full.csv

# Sky (separate CLI) — escrever directamente em public/assets/sky/
skymap2d generate "bright blue sky with soft clouds over green plains, equirectangular 360" -o ../public/assets/sky/sky.png
```

### 3. Handoff into public/

```bash
gameassets handoff \
  --profile game.yaml \
  --manifest manifest_full.csv \
  --public-dir ../public \
  --with-textures

# Se usaste -o sky.png na pasta antiga, move: mkdir -p ../public/assets/sky && mv sky.png ../public/assets/sky/
```

This creates (or refreshes):

```
public/
  assets/
    meshes/     # GLB finais (lod0/lod1/lod2, collision, etc.) — só local, .gitignore
    images/     # PNG 2D do Text2D — só local, .gitignore
    textures/   # PNG difusos (handoff), se usados
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

| Input            | Action                                |
| ---------------- | ------------------------------------- |
| W A S D          | Move (relative to camera)             |
| Space            | Jump                                  |
| G                | Save game (localStorage)              |
| H                | Load game (localStorage)              |
| B                | Drop / aim + throw a bomb             |
| V                | Cycle held weapon (sword/axe/spear)   |
| F                | Interact (chests, shrines, readables) |
| J                | Harvest / gather (primary action)     |
| K                | Trade with the merchant               |
| Right mouse drag | Orbit camera                          |
| Mouse wheel      | Zoom                                  |

## Extending

- Add more assets: edit `manifest_full.csv` (and the subset `manifest.yaml` / `manifest.boss_ogre.yaml`), re-run batch + handoff.
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
