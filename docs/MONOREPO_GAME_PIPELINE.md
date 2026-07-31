# Monorepo Game Pipeline — from GameAssets to VibeGame

This document defines a **reference layout** and **handoff contract** between the asset-generation tools (centered on [GameAssets](../GameAssets/)) and a **browser runtime** ([VibeGame](../VibeGame/)). It complements [INSTALLING.md](INSTALLING.md).

**Runtime findings (models, VRAM, Omni, mesh):** [MODEL_FINDINGS.md](MODEL_FINDINGS.md) · [findings/](findings/README.md).  
**UMS batch waves:** [GAMEASSETS_UMS_BATCH.md](GAMEASSETS_UMS_BATCH.md) · **Mission:** [mission/](mission/README.md).  
VRAM: **UMS + hw-auto** (no public `--low-vram`); editable monorepo bins: [INSTALLING.md](INSTALLING.md).

## 1. Roles

| Layer | Tools | Output |
|--------|--------|--------|
| Orchestration | `gameassets` | Per-row images, GLBs, audio paths under a chosen `output_dir` |
| 2D / maps | Text2D, Texture2D, Materialize, Skymap2D | PNG, PBR map folders, equirectangular sky |
| 3D | Text3D, Paint3D, Rigging3D | GLB (mesh + PBR; optional rig) |
| Audio | Text2Sound | WAV/FLAC (see Text2Sound docs) |
| QA | AiGameKitLab | GLB inspection (optional) |
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
3. **Batch**: `gameassets batch --profile game.yaml --manifest manifest.csv`. GPU stages ride **UMS waves** (shape → paint → optional 2D/audio/terrain; see [GAMEASSETS_UMS_BATCH.md](GAMEASSETS_UMS_BATCH.md)). Master DAG after paint is **Round 3** (rig painted → `game-pack`×1 → `text3d lod`) — [MESH_PIPELINE_FINDINGS](findings/MESH_PIPELINE_FINDINGS.md). Omni knobs / soft-fill by category: [OMNI_SHAPE_FINDINGS.md](OMNI_SHAPE_FINDINGS.md). Stages (3D, rig, animate) auto-detect from manifest + `game.yaml`; `--no-rig` / `--no-animate` / `--no-ums` to opt out.
4. **Handoff**: **`gameassets handoff --public-dir path/to/public`** copies/symlinks from the profile `output_dir` into `public/assets/…`, writes `assets/gameassets_handoff.json`, and can **prefer animated GLBs** over rigged/base when both exist. Alternatively copy files manually (see [VibeGame/examples/simple-rpg](../VibeGame/examples/simple-rpg/) for a full handoff layout).
5. **Run** the web app: `bun dev` / `npm run dev`; load GLBs as above. **Skymap2D** equirect PNG/JPG: `applyEquirectSkyEnvironment` from `vibegame` (PMREM + optional background).

**LOD0 contract:** after master pipeline promote, `meshes/{id}_lod0.glb` is the playable deliverable (animated > rigged > painted). Resume must not overwrite a promoted lod0 with painted — see [findings/MESH_PIPELINE_FINDINGS.md](findings/MESH_PIPELINE_FINDINGS.md). Rigged ladder uses geometry `text3d lod` (must weld before Decimate — V/Tri≈3 → moth-eaten LODs). Runtime tip (simple-rpg): hero uses lod0 only; distant enemies may declare `lod1-url` / `lod2-url` on `<GLTFLoader>`.

**Compression contract:** deliverable LODs should carry **KTX2** textures + **meshopt** geometry (`aigamekit-lab check` rules). Pipeline finish defaults ON; re-compress with `text3d finish`. Needs `npx @gltf-transform/cli` **and** Khronos `ktx` — see [GLB_FINISH_COMPRESSION.md](GLB_FINISH_COMPRESSION.md). VibeGame `gltf-bridge` loads both via `KTX2Loader` + `MeshoptDecoder`.

**Fellable trees:** tree-like assets get `text3d split-at-height --no-cap` before LOD → composed `Stump`+`Top` lods + `*_stump_collision.glb` for `break-style: fall`. Cut plane left open (no seal). Bipedal enemies need `animate.preset: humanoid` + Quaternius clips — see [findings/ANIMATOR_RETARGET_FINDINGS.md](findings/ANIMATOR_RETARGET_FINDINGS.md).

**Animator3D** can run **inside** `gameassets batch` (auto-detected when `animator3d` profile block exists) or **standalone** on a rigged GLB — see [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md).

**Idea-to-scaffold:** **`gameassets dream`** plans assets + scene, runs batch (auto-detecting rig + animate from the plan), skymap, handoff, and emits a Vite + VibeGame project — details in [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md).

## 4.1 Quality Presets (QualityEngine)

All generation tools support a unified `--quality` flag backed by **QualityEngine** in `aigamekit-shared`:

```bash
text3d generate prompt -o out.glb --quality high --category humanoid
text2sound generate "sword slash" -o sfx.wav --quality medium --category weapon
terrain3d generate --quality high
```

**Quality tiers** (`fast | low | medium | high | highest`):

| Tier | Speed | Resolution | Steps | Best for |
|------|-------|-----------|-------|----------|
| `fast` | ~30s | Low | Minimal | Drafts, rapid iteration |
| `low` | ~1min | Basic | Low | Prototyping |
| `medium` | ~2min | Standard | Moderate | **Default** — game-ready |
| `high` | ~5min | High | High | Production polish |
| `highest` | ~10min+ | Max | Full | Showcase / trailers |

**Category** (optional) tailors quality to asset type — e.g., `--category humanoid` uses higher resolution than `--category weapon`. Defined in `Shared/src/aigamekit_shared/data/asset-categories.yaml`.

**GameAssets integration:** set `generation: medium` in `game.yaml`. Per-row overrides: `generation: high` in `manifest.yaml`. The `generation:` key maps to `--quality` on all sub-tools.

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
