# AGENTS.md — GameAssets

Batch asset orchestrator for the GameDev pipeline. Calls text2d, text3d, paint3d,
rigging3d, animator3d, gamedev-lab, terrain3d via subprocess. Does NOT contain
mesh code itself.

## WHERE TO LOOK

| Task | File(s) | Notes |
|------|---------|-------|
| Master pipeline DAG | `pipeline.py` | Round 3: clean→paint→rig painted→game-pack×1→`text3d lod` |
| Batch execution | `batch_cmd.py` | Per-row 2D/3D/audio; UMS waves + `MasterDeferQueue` |
| UMS batch waves | `ums_coord.py`, `ums_batch.py` | `run_gpu_wave` (window≤16, `preload=False`); hw_auto peak |
| Omni soft-fill / stale | `omni_ctrl.py` | `softfill_omni_from_category`; fallback `_CATEGORY_OMNI_DEFAULTS_FALLBACK` when Text3D missing (CI); `prepare_shape_for_generation`; `shape_omni_stale` |
| **Manifest authoring** | [`docs/MANIFEST_AUTHORING.md`](../docs/MANIFEST_AUTHORING.md) | `category`/`size_m`/Omni/`text3d:` — happy path + octree×faces |
| Octree × faces (empírico) | [`docs/findings/OCTREE_FACES_FINDINGS.md`](../docs/findings/OCTREE_FACES_FINDINGS.md) | κ, char_m², simple-rpg n=67 |
| UMS batch guide | [`docs/GAMEASSETS_UMS_BATCH.md`](../docs/GAMEASSETS_UMS_BATCH.md) | Operator happy path |
| Model / mesh findings | `docs/MODEL_FINDINGS.md`, `docs/findings/` | VRAM, Omni, Round 3 DAG |
| Smart resume | `resume_cmd.py` | Checkpoint; looks in `_intermediate/` |
| Game profiles | `profile.py` | Sub-profiles + `from_dict` |
| Dream (idea to game) | `dream/` | planner / emitter / runner |
| Quality presets | `generation_profiles.py` | Maps to `--quality` |
| Asset categories | `categories.py` | Target faces + hints |
| GLB validation rules | `data/rules/*.yaml` | lod0, lod1, lod2, rigged, animated, collision |
| Handoff to VibeGame | `handoff_export.py` | Prefers animated GLB |
| TUI dashboard | `dashboard.py` | All stages (not paint-only) |

## MASTER PIPELINE (Round 3)

`run_master_pipeline()` in `pipeline.py`. GPU shape/paint usually run first via
UMS waves; master finalize is deferred (`MasterDeferQueue`) until the wave drains.

1. **generate** — `text3d generate` (raw shape; Omni soft-fill; often `batch_cmd` wave)
2. **topology-fix** — `text3d topology-fix` (`--export-origin feet|center|none`, `--fill-holes-sides N`)
3. **paint** — `paint3d` on clean / `to_paint`
4. **rig** — `rigging3d pipeline` on **`_painted`** → `_intermediate/id_rigged.glb` (not clean HI)
5. **animate** — `animator3d game-pack` **×1** → `_intermediate/id_rigged_animated.glb`
6. **lod** — `text3d lod` on animated/rigged (geometry path, **no** `--painted-mesh`) → lod0/1/2 (+ KTX2/meshopt via `_finish_lod_with_rollback`). Keeps armature/weights/clips — **no** `transfer-weights` in the DAG. Text3D must **weld** before Decimate (rigged often V/Tri≈3).
7. **collision** — from painted (as applicable; finish = dedup/prune only)
8. **validate** — `gamedev-lab check glb --category …` (rules expect `ktx2` + `meshopt` on lod0)

**Statics (no rig):** `text3d lod --painted-mesh` with `--target-faces` (LOD0≈1.2×) + `--finish-lod0` (meshopt/KTX2 ON).
**Re-compress without regen:** `text3d finish meshes/id_lod0.glb` — deps + happy path:
[`docs/GLB_FINISH_COMPRESSION.md`](../docs/GLB_FINISH_COMPRESSION.md).
**Fellable trees:** when `wants_split_at_height` (tree-like only — not rocks),
`text3d split-at-height --no-cap` → stump/top painted → LOD each → compose
`Stump`+`Top` lodN + `*_stump_collision`. Cut-only geometry (`cap=False`);
stamp `_intermediate/{id}_split_seal.txt` = `SEAL_VERSION` from
`gamedev_shared.mesh_split` (`cut-only-v1`). Resume: seal drift or
`--redo-split` / `GAMEDEV_REDO_SPLIT=1` → `invalidate_split_artifacts`
(keeps unsplit `*_painted.glb`). Details:
[`docs/findings/MESH_PIPELINE_FINDINGS.md`](../docs/findings/MESH_PIPELINE_FINDINGS.md#árvores-derrubáveis--split-at-height-antes-do-lod).

**Abolished in default DAG:** `_rigged_hi`, `transfer-weights`×LOD, `game-pack`×LOD,
bake-master-as-LOD0-before-rig. Manual `rigging3d transfer-weights` remains for
one-off rebinding.

**Promotion:** animated > rigged > painted → `meshes/{id}_lodN.glb`.
Pré-promote → `_intermediate/{id}_lodN_pre_promote.glb`.
Alias `{id}_rigged_animated.glb` ← lod0 (`publish_rigged_animated_alias`).

**Resume must not clobber promoted lod0:** skip LOD regen when
`_glb_is_promoted_animated` / `_glb_is_promoted_rigged`. Details:
[`docs/findings/MESH_PIPELINE_FINDINGS.md`](../docs/findings/MESH_PIPELINE_FINDINGS.md).

**Legacy:** `--legacy-pipeline` uses `_post_text3d_mesh_extras` instead.

## CLI

```
gameassets init|info|prompts|batch|resume|handoff|validate|dream
gameassets mesh reorigin-feet
gameassets debug screenshot|inspect|compare|bundle
gameassets skill install
```

`batch` runs the master pipeline by default. `resume` picks up from last checkpoint.
`dream` goes from text description to playable project. Flags: `--no-ums`,
`--ums-stream`, `--no-rig`, `--no-animate`, `--redo-split`, …

## ANTI-PATTERNS

- **NO mesh operations here.** Text3D owns all mesh code. GameAssets only orchestrates subprocesses.
- **NO `bpy` or `trimesh` imports.** If you need mesh work, call `text3d` or `rigging3d`.
- **NO `_intermediate/` references in runtime or game code.** Pipeline state only.
- **LOD0 must be the final deliverable.** animated > rigged > painted when those stages apply.
- **Never overwrite promoted lod0/1/2 with painted on resume.** Guard `_glb_is_promoted_*`.
- **Do not name promote archives `*_lodN_painted`.** Use `*_lodN_pre_promote`.
- **Do not reintroduce bake-master + transfer-weights as the default DAG.** Round 3 = rig painted → game-pack×1 → `text3d lod`.
- **Do not Decimate rigged/animated LODs without weld in Text3D.** V/Tri≈3 → moth-eaten LOD1/2. Fix lives in `text3d` / `smooth_shade_scene`, not GameAssets.
- **Do not enable tree cap (`--cap`) by default.** Cut-only geometry; hole closure is future work. The **version stamp** (`SEAL_VERSION` / `*_split_seal.txt`) is required — do not confuse stamp with mesh caps.
- **Do not use `animate.preset: creature` for bipedal enemies** that scripts drive with Quaternius clip names — use `humanoid` + `force_preset`.
- **UMS waves:** no sync preload for shape/paint; do not run master mid-wave (`MasterDeferQueue`).
- **Profile resolution:** generation (quality) → explicit (`game.yaml`) → defaults. `QualityEngine` fills only `None`.
- **Softfill without Text3D:** GameAssets CI does not install Text3D — `softfill_omni_from_category` must use the local category fallback, never silently leave Omni empty.
- **Fragile files:** `batch_cmd.py`, `pipeline.py`, `resume_cmd.py` — read before editing.

## DATA FILES

| File | Purpose |
|------|---------|
| `data/presets.yaml` | Style presets: lowpoly, pixel_art, painterly, realistic_stylized |
| `data/rules/*.yaml` | GLB validation rules per category |
| `cursor_skill/SKILL.md` | Cursor Agent Skill |

## TESTS

Run: `make test-gameassets` or `pytest tests/ -v` (package venv).

Key: `test_ums_batch.py` / `test_ums_coord.py`, `test_omni_softfill.py`,
`test_cli_helpers.py`, `test_profile.py`, `test_resume_master.py`,
`test_dream_emitter.py`, `test_gameassets_coverage_100.py` (CPU floor).

Monorepo guide: [`docs/TESTING.md`](../docs/TESTING.md) · [`docs/TESTING_PT.md`](../docs/TESTING_PT.md).
