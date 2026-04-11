# Monorepo Game Pipeline — from GameAssets to VibeGame

This document defines a **reference layout** and **handoff contract** between the asset-generation tools (centered on [GameAssets](../GameAssets/)) and a **browser runtime** ([VibeGame](../VibeGame/)). It complements [INSTALLING.md](INSTALLING.md).

## 1. Roles

| Layer | Tools | Output |
|--------|--------|--------|
| Orchestration | `gameassets` | Per-row images, GLBs, audio paths under a chosen `output_dir` |
| 2D / maps | Text2D, Texture2D, Materialize, Skymap2D | PNG, PBR map folders, equirectangular sky |
| 3D | Text3D, Paint3D, Part3D, Rigging3D | GLB (mesh + PBR; optional parts / rig) |
| Audio | Text2Sound | WAV/FLAC (see Text2Sound docs) |
| QA | GameDevLab | GLB inspection (optional) |
| Runtime | VibeGame | Interactive scene (ECS + Three.js) |

## 2. Recommended folder layout (handoff)

After batch generation, normalize assets into a **web project** tree (Vite `public/` or static host):

```text
my-game/
  public/
    assets/
      models/          # GLB from Text3D / Paint3D / Rigging3D (copy or symlink)
      textures/        # Optional: loose PNGs from Text2D / Texture2D
      audio/           # Text2Sound outputs
      sky/             # Skymap2D equirectangular EXR/PNG (if used as env map)
  src/
    main.ts
  index.html
```

**Naming convention (suggested):**

- `assets/models/<id>.glb` — one file per manifest row or logical prop.
- `assets/audio/<id>.<ext>` — matches CSV `id` or slug.
- `assets/sky/<name>.png` — skybox source; how you bind it in Three.js/VibeGame is up to your scene (VibeGame’s declarative layer does not yet ship a skybox recipe tied to this path — you may set scene background in code or extend the world XML later).

## 3. Web contract (minimum)

For the runtime to load content **without** a custom CMS:

| Asset type | URL pattern (dev) | Notes |
|------------|-------------------|--------|
| GLB | `/assets/models/<name>.glb` | Static props: `loadGltfToScene` from `vibegame`, `<GLTFLoader url="…">`, or `GLTFLoader`. Rigged characters with embedded clips: `loadGltfAnimated` + `GltfAnimator`, or `<PlayerGLTF model-url="…">` ([VibeGame README](../VibeGame/README.md)). |
| Audio | `/assets/audio/<name>.wav` | Use Web Audio or `<audio>`; not wired by VibeGame core — integrate in your game code |
| Sky | `/assets/sky/<name>.png` | Use as `THREE.Texture`, `Scene.background`, or PMREM — e.g. `applyEquirectSkyEnvironment` |

**Environment variables (`*_BIN`)** apply to **CLI batch** tools only, not to the browser. The browser only sees **HTTP URLs**.

## 4. Reference pipeline (minimal game)

1. **Install CLIs** (repo root): `./install.sh` for the tools you need (see [INSTALLING.md](INSTALLING.md)); include `gameassets`, `text2d`/`texture2d`, `text3d`, optional `paint3d`, `text2sound`, `animator3d` (for animated characters), `vibegame`, etc.
2. **Author** `game.yaml` + `manifest.csv` + presets ([GameAssets README](../GameAssets/README.md)).
3. **Batch**: `gameassets batch --profile game.yaml --manifest manifest.csv --with-3d`, adding **`--with-rig`** for Rigging3D when `generate_rig=true`, **`--with-animate`** for **Animator3D `game-pack`** after rig (requires `animator3d` on `PATH` or `ANIMATOR3D_BIN`; optional `animator3d` preset in `game.yaml`), **`--with-parts`** and audio columns as needed.
4. **Handoff**: **`gameassets handoff --public-dir path/to/public`** copies/symlinks from the profile `output_dir` into `public/assets/…`, writes `assets/gameassets_handoff.json`, and can **prefer animated GLBs** over rigged/base when both exist. Alternatively copy files manually (see [VibeGame/examples/simple-rpg](../VibeGame/examples/simple-rpg/) for a full handoff layout).
5. **Run** the web app: `bun dev` / `npm run dev`; load GLBs as above. **Skymap2D** equirect PNG/JPG: `applyEquirectSkyEnvironment` from `vibegame` (PMREM + optional background).

**Animator3D** can run **inside** `gameassets batch` (`--with-animate`) or **standalone** on a rigged GLB — see [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md).

**Idea-to-scaffold:** **`gameassets dream`** plans assets + scene, runs batch (including `--with-animate` when the plan includes rigged characters), skymap, handoff, and emits a Vite + VibeGame project — details in [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md).

## 5. Synergy limits (honest scope)

- **GameAssets** orchestrates batch content; **`gameassets dream`** additionally scaffolds a **playable Vite project** — you still own tuning, gameplay code, and release/CI.
- **VibeGame** favors **declarative XML**; GLB integration uses **`loadGltfToScene`**, **`loadGltfAnimated`**, **`GltfAnimator`**, `<GLTFLoader>`, or `<PlayerGLTF>` as needed.
- **Shipped production builds** (CDN, stores) still need your packaging and QA beyond the monorepo defaults.

## 6. See also

- [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md) — AI workflow, animation pipeline, `dream`
- [VibeGame/examples/hello-world/README.md](../VibeGame/examples/hello-world/README.md) — minimal Vite + terrain + `<entity place="…">`
- [VibeGame/examples/simple-rpg/README.md](../VibeGame/examples/simple-rpg/README.md) — walkable scene + full GameAssets handoff
- [GameAssets cursor skill / batch behavior](../GameAssets/src/gameassets/cursor_skill/SKILL.md)
- [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md) — Animator3D after rigging
- Root [README.md](../README.md) — project map
