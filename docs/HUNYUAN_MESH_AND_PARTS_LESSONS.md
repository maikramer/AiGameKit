# Lessons: Hunyuan3D shape, repair, and Part3D

Operational notes from simple-rpg / watchtower / chapel (~2026-07).
Portuguese (canonical detail): [`HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).
Mesh findings: [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md).
Paint: [`findings/PAINT_PART_FINDINGS.md`](findings/PAINT_PART_FINDINGS.md).

## Hunyuan3D shape (Text3D)

- **Elephant feet** / intentional plinths: fix in **generation** (prompt). `clamp_base_flare` is **off** in current `topology_clean`.
- **Thin features** (ladder, flag): seed dominates; repair cannot unstick; Part3D `faces` only cuts existing faces.
- **Plastic double-shell buildings** (chapel): outer + inward-turning base. Do **not** bisect a fake floor — `force_close_base` was removed after destroying geometry. **Hollow underside is OK** for gameplay cameras; QA `_shape` for clips/bad form, not “seal the floor.” Prefer **camera/prompt** (eye-level three-quarter) + careful morph-close; internal-shell strip is opt-in / auto for hollow categories only.
- **Doors sealed after clean**: selective watertight uses loop **diameter** guards; large openings should survive.
- **Bell / interior props deleted**: old room-scale shell removal; thin-sandwich only is safe; profile default **off**.
- Same prompt, different seed ⇒ different weld quality. On ~6 GB, CLI `--quality high` may still be **hw-auto capped**.

### Building prompts (i2m)

- Category `building`: `hint_2d` (eye-level 3/4, closed base) + `hint_3d` (no hollow shell) + negatives (worm’s-eye, underside, …).
- `prompt_builder`: for i2m refs, apply **both** `hint_2d` and `hint_3d` (view angle was previously dropped).

## Repair (`topology_clean`)

Current profile (see `mesh_repair.py`):

| Knob | Default | Note |
|------|---------|------|
| fill_holes_sides | 96 | Micro cracks; large doors kept via diameter ratio |
| watertight | True | Selective; `max_loop_diameter_ratio≈0.35` |
| remove_internal_shells | False | CLI auto-ON for building/hollow |
| flare / Taubin / force_close_base | off / removed | Destructive on plastic shells |

Pipeline: reweld → weld → slivers/debris → fill → selective watertight → optional shell strip / morph-close → shade-smooth.
Engine: `text3d topology-fix --engine arrays` (default).

## Paint

- GameAssets: `ensure_clean_for_paint` — texture `_clean`, not raw `_shape`.
- Paint3D: `paint_prep.restrict_inpaint` — do not invent texture on never-baked UV islands (interior garbage).

## Part3D

| Export | Use |
|--------|-----|
| **`faces` (default)** | Appearance; thin appendages |
| `xpart` | Chunky solids only; melts ladders/flags |
| `hybrid` | Solids + face fallback; higher risk |

Best visual watchtower parts were **face-split**, not X-Part.
Welded seed tradeoff: `p3sam` ≈ ladder peel; `--fine-parts` (hybrid, `detail-levels=0`) ≈ flag peel.
Complementary peels (same face count): `label_fuse.fuse_protrusion_labels`.
High-poly: remesh proxy + `--segmentation-proxy` + `label_transfer`.

## Recommended prop flow

`generate --no-topology-fix` → `topology-fix` → paint (`public/assets/images/`) → lod → `part3d … --parts-mode faces`.
Buildings: regenerate Text2D with eye-level framing before re-running shape if underside/hollow base appears.

## Code map

`mesh_repair.py` · `mesh_repair_arrays.py` · `mesh_lod.py` · `categories.py` · `prompt_builder.py` · `paint_prep.py` · `pipeline.ensure_clean_for_paint` · Part3D `face_split` / `label_fuse`
