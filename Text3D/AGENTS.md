# AGENTS.md — Text3D (text3d)

Text-to-3D mesh generation. Hunyuan3D-Omni (controlos geométricos; SDNQ INT4 em GPUs pequenas). Owner of ALL mesh operations across the monorepo.

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
| GLTF finish | `utils/gltf_finish.py` | Post-LOD: `[ktxdecompress]` → shade+N+T → dedup → prune → **UASTC `*normal*` + ETC1S albedo/MR/AO/emissive** → meshopt |
| Alignment | `utils/mesh_align_hunyuan.py` (142 lines) | +Z face normal to ground |
| Base plane | `utils/mesh_base_plane.py` (288 lines) | Base plane detection/removal |
| Background removal | `utils/bg_removal.py` (98 lines) | BiRefNet |
| Collision mesh | `utils/collision.py` | Modes: `hull` / `envelope` (voxel remesh côncavo) / `mesh` |
| Split at height | `utils/mesh_split.py` | Thin wrapper → `aigamekit_shared.mesh_split` (stump+top) |
| Defaults | `defaults.py` (111 lines) | Constants, presets, export rotation/origin |
| Omni controls / presets | `utils/omni_controls.py`, `omni_presets.py` | bbox max=1.0; pose Quaternius |
| Octree soft-tune por size_m | `bbox_tune.py` | `char_m=(L·H·W)^(1/3)`; não passar `octree_resolution` salvo override |
| Manifest authoring | [`docs/MANIFEST_AUTHORING.md`](../docs/MANIFEST_AUTHORING.md) | Como configurar Omni/size no GameAssets |
| Octree × faces | [`docs/findings/OCTREE_FACES_FINDINGS.md`](../docs/findings/OCTREE_FACES_FINDINGS.md) | Empírico κ / char_m² |
| vramd payload builder | `ums_payload.py` | Shared with GameAssets batch waves |
| Findings hub | `docs/MODEL_FINDINGS.md`, `docs/OMNI_SHAPE_FINDINGS.md` | VRAM / Omni / flashvdm |

## CLI COMMANDS

**Generation:** `generate`, `generate-batch`
**Pipeline:** `topology-fix`, `bake-master`, `finish` (KTX2+meshopt re-compress in-place)
**Mesh Ops:** `lod`, `remesh`, `remesh-textured`, `collision`, `split-at-height`, `align-plus-z`
**Utility:** `convert`, `doctor`, `info`, `gpu-processes`, `models`, `skill install`

## PIPELINE STAGES (GameAssets master pipeline integration)

Stage 1 — `generate`: Text/Image → raw GLB. Text2D prompt + Hunyuan3D marching cubes. Key flags: `--export-origin feet|center|none`, `--quality`, `--category`, `--preset`, `--gpu-ids`.

Stage 2 — `topology-fix`: Shared profile `topology_clean` (reweld → weld → slivers/debris → fill → selective watertight → optional shell strip / morph-close → shade-smooth). See `docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`. CLI: `--fill-holes-sides`, `--engine arrays|bpy`, `--morph-close*`, `--remove-internal-shells` / `--keep-internal-shells`, `--category`.

Stage 3 — `bake-master`: LOD0 production mesh. Decimation + normal bake from high-poly + **KTX2 híbrido (ETC1S albedo / UASTC normais) + meshopt** (defaults ON). Meshopt: bpy 5.2+ + `libmeshoptimizer-dev`; pós-KTX2 usa gltf-transform. KTX2: Node `npx @gltf-transform/cli` **+** CLI `ktx`. Intermédios bpy exportam **JPEG** (não PNG). Ver [`docs/GLB_FINISH_COMPRESSION.md`](../docs/GLB_FINISH_COMPRESSION.md).

Stage 4 — `lod`: LOD triplet (LOD0/1/2) with textured or geometry-only paths. Preserves armatures and animations intact.

Stage 5 — `collision`: `hull` (default), `envelope` (voxel remesh côncavo para arcos), ou `mesh` (só decimate).

## CRITICAL CONVENTIONS

**Export rotation:** Hunyuan3D outputs face **+Z upward**. Apply **X+90°** to stand
upright in OpenGL/WebGL **Y+ up**. This rotation must propagate through every
subsequent stage. If the mesh appears "belly-up" or **height lives on Z** starting
from `_shape`, the rotation was dropped.

**`size_m` / bbox axes (após export Y+ up):** `[L,H,W]` → `[X,Y,Z]` —
L=largura (X), H=altura (Y), W=profundidade (Z). Full map:
[`docs/OMNI_SHAPE_FINDINGS.md`](../docs/OMNI_SHAPE_FINDINGS.md) §1 ·
[`docs/MANIFEST_AUTHORING.md`](../docs/MANIFEST_AUTHORING.md) §3.

**Export origin:** `--export-origin feet` is the default for game assets (y=0 at soles). `center` for pivots at mesh center. `none` leaves raw Hunyuan origin.

**Topology fix pipeline** (`prepare_mesh_topology` → Shared profile `topology_clean`): reweld → weld → dissolve/loose → long edges → slivers → debris → fill → **selective** `make_watertight` (loop diameter guard) → optional morph-close / `--remove-internal-shells` → shade-smooth. Profile: **no** `force_close_base` (removed), **no** flare/Taubin. Bake-master / `_to_paint`: `pre_decimate_uv` OK. LOD texturado (`remesh_textured`): **só** `post_decimate` após COLLAPSE (pre-weld/`pre_decimate_uv` travam o rácio).

**Hollow / plastic-shell buildings (chapel):** do **not** bisect a fake floor — underside turns inward. Fix via Text2D **view/prompt** (eye-level three-quarter, closed foundation; `categories.building` + `prompt_builder` hint_2d+hint_3d). Details: lessons docs below.

**Hunyuan / Part3D lessons** (plastic shell, prompts, welded thins, faces vs X-Part, seed): [`docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md`](../docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md) · [PT](../docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

**LOD paths:**

| Flag | Path | Decimate |
|------|------|----------|
| `--painted-mesh` | `generate_lod_textured_glb_triplet` → `remesh_textured_glb` | **meshoptimizer** (`gltf-transform simplify`, costuras UV bloqueadas, atlas intacto); abaixo do piso de costuras → **rebake** (decimate + xatlas + closest-point). Sem `npx` cai para COLLAPSE legado |
| (omitido) | `generate_lod_glb_triplet` (Round 3: input = animated/rigged) | **meshopt-first** (`weld=False` se skinned); no piso de costuras **aceita faces acima do alvo** (COLLAPSE abaixo rasgaria UVs/weights). Sem CLI → COLLAPSE bpy + weld modo B |

Preserves armatures/animations. Manual rebind: `rigging3d transfer-weights` (fora do DAG Round 3).

**Normals / tangents:** Paint exports `NORMAL`+`TANGENT`; bpy re-exports call `smooth_shade_scene` first. Finish: `ktxdecompress` (if KTX2) → shade+T → dedup → prune `--keep-attributes` → uastc → meshopt. [`docs/GLB_FINISH_COMPRESSION.md`](../docs/GLB_FINISH_COMPRESSION.md).

**V/Tri≈3 / moth-eaten LOD:** (A) flat import → shade; (B) verts already duplicated → **must weld** before Decimate. Shade alone ≠ weld. Do **not** pre-weld healthy painted meshes (stalls ~20k → identical lod1/lod2). Details: [`MESH_PIPELINE_FINDINGS`](../docs/findings/MESH_PIPELINE_FINDINGS.md#vtri3-e-lod-moth-eaten-2026-07).

**bake-master / finish dependencies:** KTX2/UASTC = Node `npx @gltf-transform/cli` **+** CLI `ktx`. Meshopt: bpy 5.2+ + `libmeshoptimizer-dev`; pós-KTX2 → gltf-transform. `text3d doctor` checks npx/ktx/meshopt. Re-comprimir / reparar N+T: `text3d finish asset.glb`.

## ANTI-PATTERNS

**FORBIDDEN:** `normals_split_custom_set(loop_normals)` in `mesh_lod.py` or `mesh_remesh_textured.py`. Use `smooth_shade_scene` / `apply_smooth_by_angle` instead.

**FORBIDDEN:** Decimate COLLAPSE on a V/Tri≈3 mesh without weld first (LOD geometry path). Assume `smooth_shade` ≠ weld when verts are already duplicated.

**FORBIDDEN:** `gltf-transform prune` without `--keep-attributes` (strips `TANGENT`).

**FORBIDDEN:** Weld/`remove_doubles` immediately before COLLAPSE on healthy painted meshes (stalls face budget → identical lod1/lod2). Exception: the rebake route welds on purpose — the UVs are discarded there anyway.

**FORBIDDEN:** Decimate COLLAPSE with the original atlas at aggressive ratios — it collapses across UV islands and shreds the texture. Route through `meshopt_simplify_glb` (atlas preserved) or the rebake (atlas repainted). Detector: V/Tri *rises* with decimation. See [`MESH_PIPELINE_FINDINGS`](../docs/findings/MESH_PIPELINE_FINDINGS.md).

**FORBIDDEN:** `gltf-transform weld` before simplify on **skinned** GLBs — merges verts that differ only in `JOINTS`/`WEIGHTS`. Geometric LOD uses `weld=False` when `_glb_has_skins`.

**FORBIDDEN:** Forçar COLLAPSE abaixo do piso de costuras no path geométrico/rigado. Aceitar faces mais altas; rebake+`transfer_weights` está fora do DAG Round 3.

**FORBIDDEN:** Silent exception swallowing in `weld_glb` (`export.py`). Use `try/except` with `log.warning`.

**DO NOT modify vendored code** under `src/text3d/hy3dshape/` excepto patches mínimos documentados (Tencent Hunyuan3D-Omni, upstream license).

**`simplify`:** Decimate COLLAPSE via `aigamekit_shared.mesh_simplify` (stepwise ≤50%; default no pre-merge / no boundary protect). Use for `_to_paint` — **not** `remesh` (voxel).

**`simplify-textured`:** `remesh_textured_glb` (texture/UV); no `pre_decimate_uv` before COLLAPSE.

**`align-plus-z`:** Calls `align_largest_plus_z_face_normal_to_ground` with a `--min-height-ratio` guard to prevent "folding" humanoid meshes when the heuristic misidentifies the ground-facing plane.

**`split-at-height`:** Horizontal Y-up bisect into named `Stump` + `Top` meshes
(Shared `mesh_split`). **Default cut-only** (`--no-cap` / `cap=False`, no fill) —
open cut plane; `--cap` is legacy/experimental (seal caused artifacts).
`--split-files` writes `{stem}_stump.glb` / `{stem}_top.glb`. GameAssets runs
this **before** LOD for tree-like vegetation (`wants_split_at_height`), then LODs
each half and composes `Stump`+`Top`. Review: `aigamekit-lab debug cut-review`.
See [`docs/findings/MESH_PIPELINE_FINDINGS.md`](../docs/findings/MESH_PIPELINE_FINDINGS.md).

## TESTS

10 test files, 715 LOC total.

Key tests: `test_text3d_extended.py` (160L), `test_mesh_lod.py` (136L), `test_bg_removal.py` (75L), `test_gltf_finish.py` (73L), `test_collision.py` (61L).

Run: `make test-text3d` or `pytest tests/ -v` from within the `Text3D/` directory with venv active.
