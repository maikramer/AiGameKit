# AGENTS.md — GameAssets

Batch asset orchestrator for the GameDev pipeline. 28 Python files, ~12K LOC. Calls text2d, text3d, paint3d, rigging3d, animator3d, gamedev-lab, terrain3d via subprocess. Does NOT contain mesh code itself.

## WHERE TO LOOK

| Task | File(s) | Notes |
|------|---------|-------|
| Master pipeline DAG | `pipeline.py` (1798 lines) | 10 stages: generate, topology-fix, bake-master, LOD, collision, rig, animate, validate |
| Batch execution | `batch_cmd.py` (2951 lines) | Largest file. Per-row 2D/3D/audio orchestration |
| Smart resume | `resume_cmd.py` (1324 lines) | Checkpoint-based, looks in `_intermediate/` |
| Game profiles | `profile.py` (875 lines) | 12 sub-profile dataclasses + 600-line `from_dict` parser |
| Dream (idea to game) | `dream/` (5 files, 1295 lines) | `planner.py` (LLM), `emitter.py` (file gen), `runner.py` (orchestration) |
| Quality presets | `generation_profiles.py` | 5 tiers, maps to `--quality` flags |
| Asset categories | `categories.py` (549 lines) | 16 categories with target faces + hints |
| GLB validation rules | `data/rules/*.yaml` | lod0, lod1, lod2, rigged, animated, collision |
| Handoff to VibeGame | `handoff_export.py` | Copies GLBs (prefers animated) + `manifest.json` |
| TUI dashboard | `dashboard.py` (358 lines) | Textual-based real-time progress |

## MASTER PIPELINE

`run_master_pipeline()` in `pipeline.py`. Ordered stages with promotion logic:

1. **generate** — `text3d generate` (raw shape GLB; called by `batch_cmd`, not inside `pipeline.py`)
2. **topology-fix** — `text3d topology-fix` (clean mesh; `--export-origin feet|center|none`, `--fill-holes-sides N`)
3. **paint** — `paint3d texture` (PBR texture)
4. **bake-master** — `text3d bake-master` (LOD0 with normal bake + KTX2/meshopt; needs `npx @gltf-transform`)
5. **lod** — `text3d lod` (LOD1/LOD2 from LOD0)
6. **collision** — `text3d collision` (convex hull collision mesh)
7. **rig** — `rigging3d pipeline` (rig the high-poly clean mesh)
8. **transfer-weights** — `rigging3d transfer-weights --source HI --target LOD0/1/2`
9. **animate** — `animator3d game-pack` per LOD
10. **validate** — `gamedev-lab check glb` against YAML rules

**Promotion**: animated > rigged > baked > painted. Highest-stage output becomes `lodN.glb`. Earlier intermediates move to `_intermediate/`.

**Legacy**: `--legacy-pipeline` uses `_post_text3d_mesh_extras` instead.

## CLI

```
gameassets init|info|prompts|batch|resume|handoff|validate|dream
gameassets mesh reorigin-feet
gameassets debug screenshot|inspect|compare|bundle
gameassets skill install
```

`batch` runs the master pipeline by default. `resume` picks up from last checkpoint. `dream` goes from text description to playable project.

## ANTI-PATTERNS

- **NO mesh operations here.** Text3D owns all mesh code. GameAssets only orchestrates subprocesses.
- **NO `bpy` or `trimesh` imports.** If you need mesh work, call `text3d` or `rigging3d`.
- **NO `_intermediate/` references in runtime or game code.** That directory is for pipeline state only.
- **LOD0 must be the final deliverable.** If the asset has rig or animation, LOD0 must reflect the highest stage (animated > rigged > painted). Shipping LOD0 without rig/anim when the asset has them is a regression.
- **Profile resolution order**: generation (quality tier) -> explicit (`game.yaml`) -> defaults (hardcoded). `QualityEngine` fills only `None` fields.
- **Fragile files**: `batch_cmd.py` (2951L), `pipeline.py` (1798L), `resume_cmd.py` (1324L). Changes here cascade across the entire pipeline. Read before editing.

## DATA FILES

| File | Purpose |
|------|---------|
| `data/presets.yaml` | 4 built-in style presets: lowpoly, pixel_art, painterly, realistic_stylized |
| `data/rules/*.yaml` | GLB validation rules per category (lod0, lod1, lod2, rigged, animated, collision) |
| `cursor_skill/SKILL.md` | Cursor Agent Skill documentation |

## TESTS

18 test files, 2549 LOC. Run with `make test-gameassets` or `pytest tests/ -v`.

Key test files: `test_cli_helpers.py` (426L), `test_profile.py` (295L), `test_resume_master.py` (221L), `test_dream_emitter.py` (197L).
