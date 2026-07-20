# Lessons: Hunyuan3D shape, repair, and Part3D

Operational notes from simple-rpg / watchtower / chapel work (~2026-07).
Portuguese version: [`HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

## Hunyuan3D shape (Text3D)

- **Elephant feet** at ground contact are mostly generation/MC; `clamp_base_flare` only pulls radial overshoot vs mid-height. Intentional wide plinths stay wide.
- **Thin features** (ladder, flag, poles) often weld into the volume; seed dominates. Repair cannot semantically unstick them; Part3D `faces` only cuts existing faces.
- **Hollow bases** (manifold, boundary=0, no floor): `force_close_base` — must use correct up-axis (`infer_up_axis`). Fixed world-Y after glTF→Blender injects a **side slab / skirt**.
- Same prompt, different seed ⇒ different weld quality. Do not judge Part3D across seeds without regenerating shape.
- On ~6 GB GPUs, CLI `--quality high` may still be **hw-auto capped** (check `Hardware (auto)` in the log).

## Repair (`topology_clean`)

- Prefer **fill ≤32**, **skip flap-erode**, Taubin (3), flare clamp with smoothstep falloff.
- Do **not** force `fill_holes_sides ≥ 64` when watertight (old `mesh_lod` bug).
- Measure flare from mid-height center, not world origin.
- Pipeline: reweld → weld → slivers/debris → fill → watertight → force_close_base → clamp_base_flare → Taubin → shade-smooth.

## Part3D

| Export | Use |
|--------|-----|
| **`faces` (default)** | Appearance; thin appendages |
| `xpart` | Chunky solids only; melts ladders/flags; worsens feet |
| `hybrid` | Solids + face fallback; higher risk |

Best visual watchtower parts were **face-split** (~10 meshes), not X-Part solids.

| Segment | Observed on welded watchtower |
|---------|-------------------------------|
| `p3sam` (default) | Almost full **ladder**; **flag** stays in body |
| `--fine-parts` → `hybrid` (detail-levels forced **0**) | **Flag** peels; ladder splits and leaves a welded stub |

Complementary peels (same face count): `part3d.utils.label_fuse.fuse_protrusion_labels`. Hierarchical detail skips already-thin parents.

High-poly (>~200k): 50–120k remesh proxy + `--segmentation-proxy` + label transfer. Keep GPU free on 6 GB; verify LOD face counts after export.

## Recommended prop flow

See Portuguese doc §5 for full command block. Short form: `generate --no-topology-fix` → `topology-fix` → paint (image under `public/assets/images/`) → `lod` → `part3d … --parts-mode faces`.

## Code map

`mesh_repair.py` · `mesh_lod.py` · `part3d/defaults.py` · `face_split.py` · `hierarchical.py` · `label_fuse.py` · `label_transfer.py`
