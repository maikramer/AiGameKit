# AGENTS.md — Text3D (text3d)

Text-to-3D mesh generation. Hunyuan3D-2.1 SDNQ INT4. Owner of ALL mesh operations across the monorepo.

## OVERVIEW

14 CLI commands, largest surface-area of any package. 17 own files (~5800 LOC) plus vendored hy3dshape (~8400 LOC, Tencent upstream). Vendored code is excluded from lint.

Text3D is the sole authority for mesh operations (LOD, collision, simplify, remesh, remesh-textured, topology-fix, bake-master). Other packages (GameAssets) call Text3D via subprocess, never duplicate mesh logic.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| CLI entry point | `cli.py` (1665 lines) | 14 commands; `generate` alone has 30+ flags |
| Core generator | `generator.py` (343 lines) | HunyuanTextTo3DGenerator: Text2D prompt → Hunyuan pipeline |
| LOD generation | `utils/mesh_lod.py` (487 lines) | `prepare_mesh_topology`, `generate_lod_glb_triplet` |
| Textured remesh | `utils/mesh_remesh_textured.py` (904 lines) | Isotropic remesh + xatlas UV reprojection |
| Master bake | `utils/bake_master.py` (288 lines) | LOD0: decimation + tangents + KTX2 + meshopt |
| Export/format | `utils/export.py` (379 lines) | `save_mesh`, `convert_mesh`, `weld_glb` |
| GLTF finish | `utils/gltf_finish.py` (239 lines) | Post-LOD dedup + prune + UASTC + meshopt via `@gltf-transform` |
| Alignment | `utils/mesh_align_hunyuan.py` (142 lines) | +Z face normal to ground |
| Base plane | `utils/mesh_base_plane.py` (288 lines) | Base plane detection/removal |
| Background removal | `utils/bg_removal.py` (98 lines) | BiRefNet |
| Collision mesh | `utils/collision.py` (82 lines) | Convex hull + quadric decimation |
| Defaults | `defaults.py` (111 lines) | Constants, presets, export rotation/origin |

## CLI COMMANDS

**Generation:** `generate`, `generate-batch`
**Pipeline:** `topology-fix`, `bake-master`
**Mesh Ops:** `lod`, `remesh`, `remesh-textured`, `collision`, `align-plus-z`
**Utility:** `convert`, `doctor`, `info`, `gpu-processes`, `models`, `skill install`

## PIPELINE STAGES (GameAssets master pipeline integration)

Stage 1 — `generate`: Text/Image → raw GLB. Text2D prompt + Hunyuan3D marching cubes. Key flags: `--export-origin feet|center|none`, `--quality`, `--category`, `--preset`, `--gpu-ids`.

Stage 2 — `topology-fix`: Repair raw mesh. Weld → non-manifold repair → fill holes → shade-smooth. `--fill-holes-sides N` controls how aggressively holes are closed.

Stage 3 — `bake-master`: LOD0 production mesh. Decimation + normal bake from high-poly + optional KTX2 (UASTC) + meshopt compression. Requires Node.js + `npx @gltf-transform/cli`. Falls back gracefully without it (LOD0 without compression).

Stage 4 — `lod`: LOD triplet (LOD0/1/2) with textured or geometry-only paths. Preserves armatures and animations intact.

Stage 5 — `collision`: Convex hull + quadric decimation for physics mesh.

## CRITICAL CONVENTIONS

**Export rotation:** Hunyuan3D outputs face +Z upward. Apply X+90° rotation to stand upright in OpenGL Y-up convention. This rotation must propagate through every subsequent stage. If the mesh appears "belly-up" starting from `_shape`, the rotation was dropped.

**Export origin:** `--export-origin feet` is the default for game assets (y=0 at soles). `center` for pivots at mesh center. `none` leaves raw Hunyuan origin.

**Topology fix pipeline** (the `prepare_mesh_topology()` default chain): merge vertices (digits_vertex=5) → non-manifold repair (PyMeshLab) → weld (0.01% of bounding-box diagonal) → Taubin smoothing (3 iterations, volume-preserving) → isotropic adaptive remesh (3 iterations, target edge length = 1% diagonal).

**LOD and rigged meshes:** `text3d lod` preserves armatures and animations. No separate LOD path exists for rigged assets. Weight transfer to LODs is handled by `rigging3d transfer-weights`.

**bake-master dependencies:** KTX2 and meshopt require `npx @gltf-transform/cli` on PATH. `text3d doctor` checks availability. Without it, `bake-master` produces an uncompressed LOD0 and `gamedev-lab check glb` will fail `texture_format: ktx2` and `compression: meshopt` rules.

## ANTI-PATTERNS

**FORBIDDEN:** `normals_split_custom_set(loop_normals)` in `mesh_lod.py` or `mesh_remesh_textured.py`. This forces the GLTF exporter to write per-corner normals (V/Tri=3), inflating files dramatically (e.g., goblin_shape at 33 MB). Use `shade_smooth` + `auto_smooth_angle` instead.

**FORBIDDEN:** Silent exception swallowing in `weld_glb` (`export.py`). Use `try/except` with `log.warning` so pipeline failures surface in logs.

**DO NOT modify vendored code** under `src/text3d/hy3dshape/` (Tencent Hunyuan3D-2.1, upstream license).

**`simplify-textured`:** Decimates GLB preserving texture and UV via PyMeshLab when a material is present. Without texture, falls back to classic quadric decimation. Don't assume one or the other.

**`align-plus-z`:** Calls `align_largest_plus_z_face_normal_to_ground` with a `--min-height-ratio` guard to prevent "folding" humanoid meshes when the heuristic misidentifies the ground-facing plane.

## TESTS

10 test files, 715 LOC total.

Key tests: `test_text3d_extended.py` (160L), `test_mesh_lod.py` (136L), `test_bg_removal.py` (75L), `test_gltf_finish.py` (73L), `test_collision.py` (61L).

Run: `make test-text3d` or `pytest tests/ -v` from within the `Text3D/` directory with venv active.
