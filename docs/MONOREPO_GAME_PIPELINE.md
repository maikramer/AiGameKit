# Monorepo Game Pipeline — from GameAssets to a game folder

This document defines a **reference layout** and **handoff contract** between the asset-generation tools (centered on [GameAssets](../GameAssets/)) and the game folder consumed by a runtime (e.g. the native [Viber](../Viber/) engine). It complements [INSTALLING.md](INSTALLING.md).

**Runtime findings (models, VRAM, Omni, mesh):** [MODEL_FINDINGS.md](MODEL_FINDINGS.md) · [findings/](findings/README.md).  
**vramd batch waves:** [GAMEASSETS_UMS_BATCH.md](GAMEASSETS_UMS_BATCH.md) · **Mission:** [mission/](mission/README.md).  
VRAM: **vramd + hw-auto** (no public `--low-vram`); editable monorepo bins: [INSTALLING.md](INSTALLING.md).

## 1. Roles

| Layer | Tools | Output |
|--------|--------|--------|
| Orchestration | `gameassets` | Per-row images, GLBs, audio paths under a chosen `output_dir` |
| 2D / maps | Text2D, Texture2D, Materialize, Skymap2D | PNG, PBR map folders, equirectangular sky |
| 3D | Text3D, Paint3D, Rigging3D | GLB (mesh + PBR; optional rig) |
| Audio | Text2Sound | WAV/FLAC (see Text2Sound docs) |
| QA | AiGameKitLab | GLB inspection (optional) |
| Runtime | `viber` (native Bevy engine) | Interactive scene consuming the handoff folder |

## 2. Recommended folder layout (handoff)

After batch generation, normalize assets into a **game project** tree (a `public/` asset folder served or read by the runtime):

```text
my-game/
  public/
    assets/
      models/          # GLB from Text3D / Paint3D / Rigging3D (copy or symlink)
      textures/        # Optional: loose PNGs from Text2D / Texture2D
      audio/           # Text2Sound outputs
      sky/             # Skymap2D equirectangular EXR/PNG (if used as env map)
```

**Naming convention (suggested):**

- `assets/models/<id>.glb` — one file per manifest row or logical prop.
- `assets/audio/<id>.<ext>` — matches CSV `id` or slug.
- `assets/sky/<name>.png` — sky source; how the runtime binds it (background, environment) is up to the game.

## 3. Handoff contract (minimum)

| Asset type | Path pattern | Notes |
|------------|--------------|--------|
| GLB | `assets/models/<name>.glb` | Static props, rigged characters, animated characters (embedded clips). |
| Audio | `assets/audio/<name>.wav` | Wired by the runtime's audio system of choice. |
| Sky | `assets/sky/<name>.png` | Equirect PNG/JPG usable as background or environment. |

**Environment variables (`*_BIN`)** apply to **CLI batch** tools only. The runtime only sees the files in the handoff folder.

## 4. Reference pipeline (minimal game)

1. **Install CLIs** (repo root): `./install.sh` for the tools you need (see [INSTALLING.md](INSTALLING.md)); include `gameassets`, `text2d`/`texture2d`, `text3d`, optional `paint3d`, `text2sound`, `animator3d` (for animated characters), `viber` (native runtime), etc.
2. **Author** `game.yaml` + `manifest.csv` + presets ([GameAssets README](../GameAssets/README.md)).
3. **Batch**: `gameassets batch --profile game.yaml --manifest manifest.csv`. GPU stages ride **vramd waves** (shape → paint → optional 2D/audio/terrain; see [GAMEASSETS_UMS_BATCH.md](GAMEASSETS_UMS_BATCH.md)). Master DAG after paint is **Round 3** (rig painted → `game-pack`×1 → `text3d lod`) — [MESH_PIPELINE_FINDINGS](findings/MESH_PIPELINE_FINDINGS.md). Omni knobs / soft-fill by category: [OMNI_SHAPE_FINDINGS.md](OMNI_SHAPE_FINDINGS.md). Stages (3D, rig, animate) auto-detect from manifest + `game.yaml`; `--no-rig` / `--no-animate` / `--no-vramd` to opt out.
4. **Handoff**: **`gameassets handoff --public-dir path/to/public`** copies/symlinks from the profile `output_dir` into `public/assets/…`, writes `assets/gameassets_handoff.json`, and can **prefer animated GLBs** over rigged/base when both exist. Alternatively copy files manually.

**Precompute contract (colisores):** no fim do master pipeline, o batch emite o sidecar `meshes/{id}_precompute.json` (`aigamekit-lab precompute <collision> --stump <stump> --category <cat>`; header-only, sem bpy; falha → skip soft). O handoff **inline** esse bloco na row do `gameassets_handoff.json`:

```json
{ "id": "pine_dark", "model": { "url": "/assets/models/pine_dark_lod0.glb", "lod": ["…"] },
  "precompute": {
    "version": 1, "asset_id": "pine_dark", "category": "vegetation",
    "aabb": { "min": [-1, 0, -1], "max": [1, 5, 1] },
    "collider": { "shape": "capsule", "radius": 0.23, "height": 0.6, "base_y": 0 },
    "source": "stump", "collectible_hint": { "kind": "wood" } } }
```

Decisão da forma: árvores split → cápsula do AABB do `*_stump_collision.glb`
(tronco exato); `vegetation` sem stump → fatia inferior do tronco; `rock`/
`terrain` → cilindro do AABB; resto → cápsula do AABB. O runtime consome o
manifest e deriva cápsula/cilindro **sem fetch de `*_collision.glb`**.
Detalhe:
[findings/PRECOMPUTE_COLLIDERS_FINDINGS.md](findings/PRECOMPUTE_COLLIDERS_FINDINGS.md).

**LOD0 contract:** after master pipeline promote, `meshes/{id}_lod0.glb` is the playable deliverable (animated > rigged > painted). Resume must not overwrite a promoted lod0 with painted — see [findings/MESH_PIPELINE_FINDINGS.md](findings/MESH_PIPELINE_FINDINGS.md). Rigged ladder uses geometry `text3d lod` (must weld before Decimate — V/Tri≈3 → moth-eaten LODs).

**Compression contract:** deliverable LODs should carry **KTX2** textures + **meshopt** geometry (`aigamekit-lab check` rules). Pipeline finish defaults ON; re-compress with `text3d finish`. Needs `npx @gltf-transform/cli` **and** Khronos `ktx` — see [GLB_FINISH_COMPRESSION.md](GLB_FINISH_COMPRESSION.md).

**Fellable trees:** tree-like assets get `text3d split-at-height --no-cap` before LOD → composed `Stump`+`Top` lods + `*_stump_collision.glb` for `break-style: fall`. Cut plane left open (no seal). Bipedal enemies need `animate.preset: humanoid` + Quaternius clips — see [findings/ANIMATOR_RETARGET_FINDINGS.md](findings/ANIMATOR_RETARGET_FINDINGS.md).

**Animator3D** can run **inside** `gameassets batch` (auto-detected when `animator3d` profile block exists) or **standalone** on a rigged GLB — see [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md).

**Idea-to-game:** **`gameassets dream`** plans assets + scene from a natural-language description, runs batch (auto-detecting rig + animate from the plan), terrain, sky, icons and handoff, emitting `game.yaml` / `manifest.yaml` / `world.xml` — details in the [GameAssets README](../GameAssets/README.md) and `GameAssets/src/gameassets/dream/`.

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

- **GameAssets** orchestrates batch content and handoff; you still own tuning, gameplay code, and release/CI.
- **Shipped production builds** (distribution, stores) still need your packaging and QA beyond the monorepo defaults.

## 6. See also

- [GameAssets cursor skill / batch behavior](../GameAssets/src/gameassets/cursor_skill/SKILL.md)
- [ANIMATOR3D_AFTER_RIG.md](ANIMATOR3D_AFTER_RIG.md) — Animator3D after rigging
- Root [README.md](../README.md) — project map
