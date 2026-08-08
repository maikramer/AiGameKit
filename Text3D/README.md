# Text3D — AI Text/Image-to-3D Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Text-to-3D and image-to-3D generation powered by [Text2D](../Text2D) (FLUX.2 Klein SDNQ) → [Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) (image + optional bbox/pose/point/voxel controls; SDNQ INT4 on small GPUs). Outputs geometry-only GLB/PLY/OBJ meshes. For texturing and PBR, use [Paint3D](../Paint3D) or [GameAssets](../GameAssets).

Text3D is also the **central mesh operations hub** in the monorepo — it owns all mesh post-processing (LOD, collision, remesh, simplify, align).

[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)

## Overview

Text3D generates 3D meshes in two phases:

1. **Text2D** (text → reference image) — uses FLUX.2 Klein with CPU offload by default; the model is **always unloaded** before loading Hunyuan3D.
2. **Hunyuan3D-Omni** (image → mesh) — SiT flow matching + optional geometric controls; SDNQ INT4 on small GPUs.

Generation presets (`--preset`) adjust steps, octree resolution, and chunk count together:

| Preset | Steps | Octree | Chunks | Profile |
|--------|-------|--------|--------|---------|
| `fast` | 20 | 128 | 4 096 | Minimal VRAM, fastest |
| `balanced` | 30 | 256 | 8 000 | ~6 GB VRAM, good quality |
| `hq` | 50 | 384 | 20 000 | Large GPU, highest quality |

After generation, use ``--no-topology-fix`` (recommended in the master pipeline) for a raw shape, then ``text3d topology-fix`` (Shared profile ``topology_clean``) as Stage 2. Without that flag, ``generate`` still runs topology repair in-process (see [Mesh Topology](#mesh-topology)).

> **License:** Tencent Hunyuan3D-Omni weights are under the [Tencent Hunyuan Community License](https://huggingface.co/tencent/Hunyuan3D-Omni) — territory restrictions apply. Text2D (FLUX SDNQ) license: see [Text2D/README](../Text2D/README.md) and root [README](../README.md).

## Installation

### Requirements

| Config | Minimum | Recommended |
|--------|---------|-------------|
| Python | 3.13+ | Pinned `>=3.13,<3.14` |
| GPU | Optional | CUDA ~6 GB+ (defaults tuned for this) |
| RAM | 16 GB | 32 GB |
| Disk | ~20 GB free | More (Hugging Face cache) |

### Monorepo install

```bash
cd Shared && pip install -e .
cd Text3D && pip install -e .
```

Or via the unified installer:

```bash
./install.sh text3d
```

### Manual / advanced

```bash
cd Text3D
python -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt
pip install -e .
```

**Windows:** `python -m venv .venv` and `.\.venv\Scripts\Activate.ps1`; or `scripts\setup.ps1`.

## Commands

Entry point: `text3d` (or `python -m text3d`).

### `text3d generate [PROMPT]`

Text-to-3D (Text2D → Hunyuan3D) or image-to-3D (`--from-image`).

```bash
# Text-to-3D (default: balanced preset, feet origin)
text3d generate "a futuristic robot" -o robot.glb

# High quality (large GPU)
text3d generate "chair" --preset hq -o chair.glb

# Fast generation (less VRAM/time)
text3d generate "chair" --preset fast -o chair_fast.glb

# Image-to-3D only (skip Text2D)
text3d generate -i ref.png -o mesh.glb

# Omni pose: Quaternius T-pose (packaged bone.txt — good for rig)
text3d generate -i hero.png --pose-preset quaternius-tpose -o hero_shape.glb
# Anão / chibi / cabeça grande (ombros mais baixos)
text3d generate -i goblin.png --pose-preset quaternius-tpose-dwarf -o goblin_shape.glb

# Omni bbox aspect (sword tall / thin)
text3d generate -i sword.png --bbox-preset sword -o sword_shape.glb

# Explicit size L,H,W (normalized 0–1) or meters via GameAssets size_m.
# After export: [L,H,W] → WebGL [X,Y,Z] with Y+ up (L=width/X, H=height/Y, W=depth/Z).
# See docs/OMNI_SHAPE_FINDINGS.md §1.
text3d generate -i crate.png --size 1,1,1 -o crate_shape.glb

# Point / voxel anchors (keep geometry, change appearance)
text3d generate -i new_skin.png --control-type point --point-cloud old_shape.glb -o variant.glb
text3d generate -i tower.png --control-type voxel --voxel-mesh blockout.glb -o tower_shape.glb

# Low VRAM (~6 GB): hw-auto applies SDNQ INT4 automatically
text3d generate "object" --preset balanced

# Reproducible seed
text3d generate "sword" --seed 42 -o sword.glb

# Skip mesh repair (keep high-poly for Paint3D texturing)
text3d generate "statue" --skip-remesh -o statue.glb

# Multi-GPU (split Hunyuan weights across GPUs)
text3d generate "scene" --gpu-ids 0,1 -o scene.glb
```

### Omni geometric controls

Hunyuan3D-Omni accepts **one** control signal per generation. Packaged assets live under `text3d/data/omni/` (T-pose bones + reference GLB).

Operational findings (bbox axis max=1 vs clip, presets, `size_m`, decode bounds, failure modes): [`docs/OMNI_SHAPE_FINDINGS.md`](../docs/OMNI_SHAPE_FINDINGS.md). Pose bench assets: [`docs/bench_omni/`](../docs/bench_omni/).

| Control | CLI | Use |
|---------|-----|-----|
| Pose | `--pose-preset quaternius-tpose` | Humanoid T-pose for SkinTokens / animate |
| Pose (short) | `--pose-preset quaternius-tpose-dwarf` | Dwarf/chibi T-pose (aliases: `dwarf-tpose`, `chibi-tpose`) |
| BBox | `--bbox-preset sword\|humanoid\|door\|…` or `--bbox L,H,W` / `--size L,H,W` | Aspect / relative size |
| Point | `--control-type point --point-cloud mesh.glb` | Shape lock / variant refresh |
| Voxel | `--control-type voxel --voxel-mesh blockout.glb` | Blockout → styled mesh |

`--category humanoid` soft-fills pose A-pose (`quaternius-apose`) when no control is explicit. A sidecar `*.glb.omni.json` is written next to the shape for GameAssets resume invalidation.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --from-image` | path | None | Input image path (image-to-3D mode, skips Text2D) |
| `-o, --output` | path | `outputs/meshes/<name>_<ts>.glb` | Output file path |
| `-f, --format` | str | `glb` | Output format: `glb`, `ply`, `obj` |
| `--preset` | str | `balanced` | Generation preset: `fast`, `balanced`, `hq` |
| `--steps` | int | 50 | Hunyuan3D inference steps (`DEFAULT_HY_STEPS`) |
| `--octree-resolution` | int | 384 | Octree resolution (VRAM in decode phase) |
| `--num-chunks` | int | 20 000 | Surface extraction chunks |
| `--mc-level` | float/`auto` | `auto` | Marching cubes iso-surface level (`auto` resolves to the numeric `DEFAULT_MC_LEVEL`) |
| `--guidance` | float | 4.5 | Hunyuan3D guidance scale (`DEFAULT_HY_GUIDANCE`) |
| `-W, --image-width` | int | 2048 | Text2D reference image width |
| `-H, --image-height` | int | 2048 | Text2D reference image height |
| `--t2d-steps` | int | 8 | Text2D inference steps |
| `--t2d-guidance` | float | 1.0 | Text2D guidance |
| `--model` | str | None | Text2D model ID override (default: `TEXT2D_MODEL_ID` or official BFL base) |
| `--t2d-full-gpu` | flag | false | Load Text2D fully on GPU (~12 GB+ VRAM required) |
| `--seed` | int | None | Reproducible seed for Text2D and Hunyuan3D |
| `--cpu` | flag | false | Force CPU inference (much slower) |
| `--no-remove-bg` | flag | false | Skip background removal (BiRefNet) |
| `--sdnq-preset` | str | None | SDNQ quantization: `sdnq-uint8`, `sdnq-int8`, `sdnq-int4`, `sdnq-fp8`, `none` |
| `--export-origin` | str | `feet` | Origin placement: `feet`, `center`, `none` |
| `--export-rotation-x-deg` | float | None | X-axis rotation in degrees (overrides env var) |
| `--save-reference-image` | flag | false | Save the Text2D reference image alongside output |
| `--no-prompt-optimize` | flag | false | Skip automatic prompt optimization (anti-ground-plane terms) |
| `--profile` | flag | false | Enable profiling (JSONL + SQLite) |
| `--max-faces` | int | 40 000 | Max faces via PyMeshLab quadric edge collapse (0 = no reduction) |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU weight splitting (e.g. `0,1`) |
| `--skip-remesh` | flag | false | Skip isotropic remeshing in mesh topology prep (keeps high-poly) |
| `--allow-shared-gpu` | flag | false | Allow GPU sharing with other processes |
| `--gpu-kill-others` | flag | false | **DEPRECATED:** terminate competing GPU processes |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--category` | str | None | Asset category for automatic tuning (e.g. `humanoid`, `weapon`, `prop`) |
| `--hw-auto/--no-hw-auto` | flag | on | Hardware auto-detection: fills steps/octree/chunks, SDNQ, multi-GPU ids and volume decoder from detected VRAM. Explicit flags, `--quality` and `--preset` always win. Env kill-switch: `TEXT3D_HW_AUTO=0` |
| `--volume-decoder` | str | `vanilla` | VAE volume decoder: `vanilla` (dense, original), `hierarchical` (near-surface only, ~lossless, much faster), `flashvdm` (fastest, slight quality loss) |
| `--mc-algo` | str | None | Surface extraction: `mc` (skimage, CPU) or `dmc` (GPU, requires `diso`) |
| `--compile` | flag | false | `torch.compile` on DiT+VAE+conditioner (slow first-run warmup; pays off in batch) |
| `--sage-attn` | flag | false | SageAttention INT8 attention kernels (requires `sageattention`, Ampere+) |
| `--sdnq-matmul` | flag | false | SDNQ quantized INT8 matmul (use together with `--sdnq-preset`) |
| `--control-type` | str | `none` | Omni: `none`, `bbox`, `pose`, `point`, `voxel` |
| `--pose-preset` | str | None | Packaged pose (`quaternius-tpose` / `quaternius-tpose-dwarf`); implies pose control |
| `--pose-file` | path | None | Custom bone-points txt for pose |
| `--bbox-preset` | str | None | Aspect preset (`sword`, `humanoid`, `door`, …) |
| `--bbox` / `--size` | CSV | None | L,H,W (3) or AABB (6); `--size` is 3-float alias |
| `--point-cloud` | path | None | Mesh/PLY shape anchor |
| `--voxel-mesh` | path | None | Mesh/PLY volume/blockout anchor |

> **Preset precedence:** When `--preset` is set, it overrides `--steps`, `--octree-resolution`, and `--num-chunks`. When `--quality` is set, the QualityEngine resolves preset/guidance/steps/octree/chunks only if the user hasn't explicitly provided them.

### `text3d generate-batch MANIFEST`

Batch image-to-3D from a JSON manifest. Each item must have `id`, `image`, and `output` fields. The Hunyuan3D model stays loaded across all items.

```bash
text3d generate-batch manifest.json --output-dir ./outputs --preset balanced --force
```

**Manifest format:**

```json
[
  {"id": "item1", "image": "ref1.png", "output": "item1.glb"},
  {"id": "hero", "image": "hero.png", "output": "hero_shape.glb", "pose_preset": "quaternius-tpose", "category": "humanoid"},
  {"id": "sword", "image": "sword.png", "output": "sword_shape.glb", "bbox_preset": "sword", "steps": 28, "seed": 42}
]
```

Per-item Omni fields: `control_type`, `bbox`, `bbox_preset`, `size` / `size_m`, `pose_preset`, `pose_file`, `point_cloud`, `voxel_mesh`, `category`.

Accepts all generation flags (`--preset`, `--steps`, `--guidance`, `--octree-resolution`, `--num-chunks`, `--mc-level`, `--sdnq-preset`, `--export-origin`, `--gpu-ids`, `--allow-shared-gpu`, `--force`) plus the inference acceleration flags (`--volume-decoder`, `--mc-algo`, `--compile`, `--sage-attn`, `--sdnq-matmul`). In batch mode the BiRefNet background remover stays loaded across items (no per-item reload).

### Hardware auto-detection

`--hw-auto` (default **on**) detects the available CUDA hardware and resolves a profile — only filling parameters you didn't set explicitly (`--quality`, `--preset` and individual flags always win). Check the detected profile with `text3d doctor`. Disable with `--no-hw-auto` or `TEXT3D_HW_AUTO=0`.

| Detected hardware | Profile |
|-------------------|---------|
| Multi-GPU (e.g. 2× RTX 3060 12GB = 24GB) | `gpu_ids` auto-set (accelerate weight split), hq params (30/384/20000), no quantization |
| Single GPU ≥ 10GB | hq params, no quantization |
| Single GPU 7.5–10GB | balanced params (24/256/8000), no quantization |
| Single GPU 5–7.5GB (e.g. RTX 4050 6GB) | balanced params + SDNQ INT4 |
| Single GPU < 5GB | fast params (18/128/4096) + SDNQ INT4 |
| No CUDA | fast params on CPU |

All CUDA profiles default the volume decoder to `hierarchical` (near-lossless). Multi-GPU weight splitting counts the **sum** of VRAM for the tier; an explicit `--gpu-ids` always overrides the auto selection.

### Inference acceleration

All acceleration paths are **opt-in** — defaults keep the original behavior (dense `VanillaVolumeDecoder` + skimage marching cubes), except `--hw-auto` above which is on by default.

| Flag | What it does | Trade-off |
|------|--------------|-----------|
| `--volume-decoder hierarchical` | Coarse-to-fine volume decode: queries the geo-decoder only near the surface instead of the full dense grid (e.g. 385³ ≈ 57M points at octree 384) | Near-lossless; biggest single speedup of the decode phase |
| `--volume-decoder flashvdm` | Hierarchical + FlashVDM adaptive top-k KV selection in the decoder cross-attention | Fastest; slight quality loss on fine detail |
| `--mc-algo dmc` | Differentiable marching cubes on GPU (`diso`) instead of skimage on CPU | Requires `pip install diso` and CUDA; falls back to `mc` otherwise |
| `--compile` | `torch.compile` over DiT, VAE and conditioner | First inference pays compile warmup; best for `generate-batch` |
| `--sage-attn` | INT8 SageAttention kernels in the DiT and decoder attention | Requires `pip install sageattention`, Ampere+ GPU |
| `--sdnq-matmul` | INT8 quantized matmul for SDNQ-quantized weights | Only meaningful with `--sdnq-preset` |

Recommended fast profile for batch asset production:

```bash
text3d generate-batch manifest.json --preset balanced --volume-decoder hierarchical --mc-algo dmc --compile
```

### `text3d lod MESH`

Generate a LOD triplet (LOD0/LOD1/LOD2) using quadric decimation. Preserves armatures and animations — no separate path needed for rigged LOD.

```bash
# Basic LOD generation
text3d lod model.glb -o ./out_dir --basename prop

# With textured LOD0 (Paint3D output)
text3d lod model.glb -o ./out_dir --painted-mesh painted.glb --target-faces 30000
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | **required** | Output directory for the three GLB files |
| `-n, --basename` | str | input filename stem | Output basename |
| `--lod1-ratio` | float | 0.42 | LOD1 face ratio relative to original |
| `--lod2-ratio` | float | 0.14 | LOD2 face ratio relative to original |
| `--min-faces-lod1` | int | 500 | Minimum face count for LOD1 |
| `--min-faces-lod2` | int | 150 | Minimum face count for LOD2 |
| `--meshfix` | flag | false | Apply pymeshfix (fill small boundaries only) |
| `--painted-mesh` | path | None | Painted GLB for textured LOD (LOD0=painted, LOD1 tex/2, LOD2 tex/4) |
| `--target-faces` | int | None | Target face count for LOD0 (with `--painted-mesh`) |

**Output files:** `{basename}_lod0.glb`, `{basename}_lod1.glb`, `{basename}_lod2.glb`.

### `text3d remesh MESH`

Isotropic remesh — geometry only, no texture preservation. Ideal for simplifying before texturing with Paint3D.

```bash
text3d remesh model.glb -o simplified.glb --target-faces 24000
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output GLB file |
| `--target-faces` | int | **required** | Target face count (min 4) |

### `text3d remesh-textured MESH`

Remesh with texture reprojection — preserves UV layout and materials. Re-meshes to uniform triangles (pymeshlab isotropic) then re-projects the original texture via xatlas + closest-point sampling.

```bash
text3d remesh-textured painted.glb -o remeshed.glb --target-faces 6000
text3d remesh-textured model.glb -o out.glb --target-faces 10000 --texture-size 4096
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output GLB file |
| `--target-faces` | int | **required** | Target face count (min 4) |
| `--texture-size` | int | 2048 | Output texture resolution in pixels |

### `text3d collision MESH`

Generate a simplified collision mesh (convex hull + quadric decimation) suitable for physics in Unity/Godot/Unreal.

```bash
text3d collision model.glb -o collision.glb
text3d collision model.glb -o coll.glb --max-faces 500 --no-convex-hull
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output collision GLB |
| `--max-faces` | int | 300 | Target face count for collision mesh |
| `--convex-hull/--no-convex-hull` | flag | true | Apply convex hull before simplification |

### `text3d finish GLB`

Finalize a deliverable GLB: tangents → dedup → prune → **KTX2/UASTC** → **meshopt** (defaults ON). Use to re-compress assets without regenerating the pipeline.

```bash
text3d finish hero_lod0.glb
text3d finish hero_lod0.glb -o hero_opt.glb --no-tangents
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | in-place | Output GLB (omit to overwrite input) |
| `--ktx2/--no-ktx2` | flag | true | UASTC → `image/ktx2` (needs `ktx` CLI) |
| `--meshopt/--no-meshopt` | flag | true | `EXT_meshopt_compression` |
| `--tangents/--no-tangents` | flag | true | Recalculate MikkTSpace tangents |
| `--dedup/--no-dedup` | flag | true | gltf-transform dedup |
| `--prune/--no-prune` | flag | true | gltf-transform prune |

**Deps:** Node `npx @gltf-transform/cli` **and** Khronos `ktx` (KTX-Software). Meshopt prefers bpy 5.2+ + `libmeshoptimizer-dev`. Check with `text3d doctor`. Full guide: [`docs/GLB_FINISH_COMPRESSION.md`](../docs/GLB_FINISH_COMPRESSION.md).

### `text3d align-plus-z MESH`

Align the largest +Z face normal to the ground plane. Useful for correcting models generated flat-side-up (e.g., crystal/pedestal orientation). Includes a height-ratio guard to avoid "folding" humanoid models when the heuristic fails.

```bash
text3d align-plus-z model.glb -o corrected.glb
text3d align-plus-z model.glb -o corrected.glb --min-height-ratio 0.3
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output GLB file |
| `--min-height-ratio` | float | 0.25 | Minimum height ratio guard (0–1); aborts if result is too flat |

### `text3d convert INPUT`

Convert mesh between formats (PLY, OBJ, GLB).

```bash
text3d convert mesh.ply --output mesh.glb
text3d convert mesh.obj -o mesh.glb --rotate
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto (`.glb` extension) | Output file path |
| `-r, --rotate` | flag | false | Apply orientation rotation |

### `text3d gpu-processes`

Show current GPUs and compute processes via Shared NVML helpers (fallback `nvidia-smi`). Useful when exclusive-GPU checks fail or to identify processes consuming VRAM.

```bash
text3d gpu-processes
```

### `text3d doctor`

Check PyTorch, CUDA, VRAM, and **bake/finish tooling**: bpy meshopt runtime (`libmeshoptimizer`), `npx` + `@gltf-transform/cli`, and `ktx` (KTX-Software — required for UASTC/KTX2).

```bash
text3d doctor
```

### `text3d info`

Show system info, GPU details, HF cache path, and default output directory.

```bash
text3d info
```

### `text3d models`

List all models used by Text3D (Text2D, Hunyuan3D shape, Hunyuan3D Paint reference).

```bash
text3d models
```

### `text3d skill install`

Install the Cursor Agent Skill (`.cursor/skills/text3d/SKILL.md`) into a game project.

```bash
text3d skill install --target ./my-game --force
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Game project root directory |
| `--force` | flag | false | Overwrite existing SKILL.md |

## Mesh Topology

`prepare_mesh_topology` (Shared profile `topology_clean`) repairs marching-cubes artifacts. In the master pipeline, Stage 1 `generate --no-topology-fix` keeps the raw shape; Stage 2 `topology-fix` runs the clean. `generate-batch` defaults to `--no-topology-fix` for the same reason.

| Step | Detail |
|------|--------|
| Sanitize / reweld | NaN guard + coincident reweld |
| Weld | Exact `1e-5` + density-adaptive distance |
| Long edges / slivers / debris | Outlier fans, needles, tiny islands |
| Fill holes | `--fill-holes-sides` (profile ~96 when omitted) |
| Watertight | Selective caps + fill; **loop diameter ratio** limits door/window seals |
| Optional | `--morph-close*` (thin double-shell); `--remove-internal-shells` (auto building) |
| Shade-smooth | 60° auto-smooth (no custom split normals) |

**Not in profile:** `force_close_base` (removed), base-flare clamp, Taubin. Plastic-shell / open underside buildings: fix with **Text2D view/prompt** (eye-level three-quarter, closed foundation), not a bisected floor.

Default engine: `--engine arrays` (perf study: [`docs/TOPOLOGY_FIX_GPU_STUDY.md`](../docs/TOPOLOGY_FIX_GPU_STUDY.md)). Lessons: [`docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md`](../docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md) · [PT](../docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

LOD / bake-master use profile `pre_decimate_uv` before Decimate (UV-safe weld `0.0005`, slivers, no watertight).

## Unified Model Server (vramd)

`text3d generate` auto-delegates to **`vramd`** — the monorepo GPU supervisor (one process, one socket, job queue with priority + VRAM affinity, weight+LRU eviction, subprocess workers per tool). Auto-starts on first generate unless `VRAMD_AUTO_START=0`.

```bash
text3d generate "a dragon" -o dragon.glb --vramd-stream    # queue/progress events
text3d generate "a dragon" -o dragon.glb --vramd-priority batch
text3d generate "a dragon" -o dragon.glb --no-vramd        # force in-process
vramd status                                               # backends + HOLDING/QUEUE
vramd respawn text3d                                       # reload edited src/ code
```

Models: [Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) shape (SDNQ INT4 on small GPUs; public, Tencent Community License; bbox/pose/point/voxel controls) + Text2D FLUX reference image (see [Text2D/README](../Text2D/README.md) for the 9B gate). Full guide: [`Vramd/README.md`](../Vramd/README.md).

## Quality Presets

Quality tiers map to generation presets via the [QualityEngine](../Shared/src/aigamekit_shared/quality.py) (`quality-profiles.yaml`):

| Quality | Text3D Preset | Guidance | Notes |
|---------|---------------|----------|-------|
| `fast` | `fast` | 5.0 | Minimal VRAM, ~30s per asset |
| `low` | `fast` | 5.0 | Basic quality, ~1min |
| `medium` | `balanced` | 5.0 | Standard quality (default), ~2min |
| `high` | `hq` | 5.0 | High quality, ~5min, large GPU |
| `highest` | `hq` | 5.0 | Maximum quality, ~10min+ |

The QualityEngine uses **soft resolution** — it only fills defaults when the user hasn't explicitly set a parameter. Use `--quality` and optionally `--category` to let the engine tune parameters automatically, or override individual flags as needed.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXT3D_BIN` | Override `text3d` binary path |
| `TEXT2D_MODEL_ID` | HuggingFace model ID override for the Text2D phase |
| `HF_HOME` | HuggingFace cache directory (default: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA memory config (auto-set to `expandable_segments:True` if empty by the CLI) |
| `TEXT3D_ALLOW_SHARED_GPU` | Allow GPU sharing (`1`/`true`/`yes` = allow) |
| `TEXT3D_GPU_KILL_OTHERS` | Control GPU process termination (`0` = off, `1` = force, empty = follow CLI) |
| `TEXT3D_EXPORT_ROTATION_X_DEG` | X rotation in degrees when exporting mesh |
| `TEXT3D_EXPORT_ROTATION_X_RAD` | Alternative in radians |
| `TEXT3D_EXPORT_ORIGIN` | Origin mode: `feet`, `center`, `none` |
| `AIGAMEKIT_PROFILE_LOG` | Path for profiler JSONL output (used with `--profile`) |

## Output Layout

Default output directory: `outputs/meshes/`.

| File type | Naming |
|-----------|--------|
| Generated mesh | `<prompt>_<timestamp>.glb` (or custom via `-o`) |
| LOD triplet | `{basename}_lod0.glb`, `{basename}_lod1.glb`, `{basename}_lod2.glb` |
| Collision mesh | `{basename}_collision.glb` (custom via `-o`) |
| Reference image | `{stem}_text2d.png` (with `--save-reference-image`) |
| Input copy | `{stem}_input.png` (with `--save-reference-image` + `--from-image`) |

Supported formats: GLB (default), PLY, OBJ.

## Pipeline Integration

Text3D is the **central mesh operations hub** in the monorepo. It owns all mesh operations:

- **LOD generation** — `text3d lod` (preserves armatures/animations)
- **Collision mesh** — `text3d collision`
- **Remesh (geometry)** — `text3d remesh`
- **Remesh (textured)** — `text3d remesh-textured`
- **Simplify** — via `--max-faces` in generate
- **Mesh alignment** — `text3d align-plus-z`
- **Format conversion** — `text3d convert`

[GameAssets](../GameAssets) **delegates all mesh ops to Text3D subprocesses** — it does not contain mesh code. The recommended full pipeline:

```
text3d generate → paint3d texture → text3d lod → text3d collision → game handoff
```

Or via [GameAssets](../GameAssets):

```bash
gameassets batch --manifest manifest.csv
```

For PBR maps from a diffuse image (not GLB), use [Materialize](../Materialize) / `texture2d materialize`.

## Python API

```python
from text3d import HunyuanTextTo3DGenerator
from text3d.utils import save_mesh

# Text-to-3D
with HunyuanTextTo3DGenerator(verbose=True) as gen:
    mesh = gen.generate(prompt="a red car")
    save_mesh(mesh, "car.glb", format="glb")

# Image-to-3D (skip Text2D)
with HunyuanTextTo3DGenerator(verbose=True) as gen:
    mesh = gen.generate_from_image("ref.png")
    save_mesh(mesh, "mesh.glb")
```

## Development

```bash
cd Text3D && pip install -e ".[dev]"
pytest tests/
ruff check .
ruff format .
```

Full CI: `make check` (from repo root) runs lint, format check, typecheck, and tests.

## Additional Documentation

| File | Description |
|------|-------------|
| [docs/INSTALL.md](docs/INSTALL.md) | Detailed install guide |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Troubleshooting |
| [docs/EXAMPLES.md](docs/EXAMPLES.md) | Advanced examples |
| [docs/API.md](docs/API.md) | Python API reference |
| [docs/PBR_MATERIALIZE.md](docs/PBR_MATERIALIZE.md) | PBR pipeline (Paint3D + Materialize) |

## Credits

- **Tencent Hunyuan3D-Omni** — [GitHub](https://github.com/Tencent-Hunyuan/Hunyuan3D-Omni), [HuggingFace](https://huggingface.co/tencent/Hunyuan3D-Omni) (image + bbox/pose/point/voxel; SDNQ INT4 on small GPUs)
- **Text2D** — FLUX.2 Klein (official BFL fp16 base + SDNQ runtime by default; optional pre-quantized mirror via `TEXT2D_MODEL_ID`) in the monorepo `text2d` package

## License

MIT
