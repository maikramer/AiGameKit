# Part3D — Semantic 3D Part Decomposition

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Semantic decomposition of 3D meshes via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentation and part generation. Optimized for ~6 GB VRAM with sequential CPU offloading, SDNQ quantization, pre-quantized DiT (quanto qint8), and `torch.compile`. Mesh I/O and post-decode repair use **bpy** (same stack as Text3D) — no PyMeshLab.

## Overview

Part3D splits a single textured mesh into semantically meaningful parts — e.g., a character into body, head, arms — using two stages:

1. **P3-SAM** — segments the mesh surface into part regions.
2. **X-Part** — generates separate 3D geometry for each detected part.

The tool auto-tunes parameters based on mesh geometry and available VRAM, or you can set them explicitly. It integrates with the [QualityEngine](../Shared/src/gamedev_shared/quality.py) preset system for cross-tool quality control.

**Requirements:**

- Python **3.13**
- NVIDIA GPU with CUDA (~6 GB VRAM recommended; works with offloading + SDNQ on less)
- `torch-scatter` and `torch-cluster` (install after PyTorch if needed)

## Installation

### Official (monorepo)

From the **GameDev** repo root:

```bash
./install.sh part3d
```

Or manually:

```bash
cd Shared && pip install -e .
cd ../Part3D && pip install -e .
```

## Commands

**Entry point:** `part3d` / `python -m part3d`

```
part3d --help
part3d decompose --help
```

### `part3d decompose MESH`

```bash
# Basic — auto-tuned, medium quality, hw-auto
part3d decompose character.glb

# Explicit output + verbose
part3d decompose character.glb -o output/character_parts.glb -v

# Segment only (no part generation)
part3d decompose character.glb --segment-only

# Fine architectural parts through an aligned 100k-face analysis proxy
text3d remesh chapel_high.glb -o chapel_proxy.glb --target-faces 100000
part3d decompose chapel_high.glb --segmentation-proxy chapel_proxy.glb \
  --fine-parts --quality highest

# Quality presets
part3d decompose character.glb --quality fast
part3d decompose character.glb --quality highest --no-quantize-dit

# Explicit SDNQ int4 (low VRAM)
part3d decompose character.glb -q int4

# Multi-GPU: DiT on GPU 0, auxiliaries on GPU 1
part3d decompose input.glb -o output/parts.glb --gpu-ids 0,1

# Reproducible
part3d decompose character.glb --seed 42 --steps 25 --octree-resolution 256
```

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `MESH` | path | — | Input mesh (`.glb` / `.obj`) |
| `-o, --output` | path | `{stem}_parts.glb` | Decomposed parts GLB |
| `--output-segmented` | path | `{stem}_segmented.glb` | Segmented mesh (colors per part) |
| `--segmentation-proxy` | path | None | Aligned analysis mesh; labels and output retain the original mesh topology |
| `--octree-resolution` | int | quality/auto | Octree resolution |
| `--steps` | int | quality/auto | DiT inference steps |
| `--num-chunks` | int | quality/auto | Marching cubes chunks |
| `--seed` | int | None | Reproducible seed |
| `--quality` | str | `medium` | `fast` / `low` / `medium` / `high` / `highest` |
| `--category` | str | None | Asset category overrides |
| `--no-auto-tune` | flag | off | Use fixed defaults.py values |
| `--no-cpu-offload` | flag | off | Keep all components on GPU (>10 GB) |
| `--device` | str | None | Force `cuda` / `cpu` |
| `--segment-only` | flag | off | Segment without part generation |
| `-v, --verbose` | flag | off | Verbose logging |
| `-q, --quantization` | str | `auto` | `auto` / `none` / `int8` / `int4` → SDNQ |
| `--no-quantize-dit` | flag | off | Skip DiT quantization |
| `--torch-compile` / `--no-torch-compile` | | off | `torch.compile` on DiT |
| `--no-attention-slicing` | flag | off | Disable attention slicing |
| `--hw-auto/--no-hw-auto` | | on | Soft hw profile; kill-switch `PART3D_HW_AUTO=0` |
| `--allow-shared-gpu` | flag | off | Allow GPU sharing |
| `--gpu-kill-others/--no-gpu-kill-others` | | off | Terminate competing GPU processes |
| `--profile` | flag | off | Timing / VRAM profiling |
| `--gpu-ids` | str | None | Multi-GPU IDs (e.g. `0,1`) |

## Segmentation quality

P3-SAM labels go through a **crease-aware refinement** pass by default
(`--refine-labels`, CPU-only): small label islands are absorbed and label
boundaries are snapped to sharp/concave edges (ICM over a Potts model), so a
door boundary follows the door frame instead of bleeding into the wall.

Anti-aggregation knobs:

| Flag | Default | Effect |
|------|---------|--------|
| `--refine-labels/--no-refine-labels` | on | Crease-aware boundary snap + island cleanup |
| `--merge-bbox-iou` | 0.7 | Mask-cluster merge threshold (upstream 0.5 fuses door+frame; higher = less fusion) |
| `--threshold` | 0.99 | Post-process merge threshold (higher = keeps small parts) |
| `--no-postprocess` / `--fine-parts` | off | Skip part merging entirely |
| `--mask-nms-iou` | 0.9 | IoU used to suppress redundant primary masks |
| `--secondary-mask-iou` | 0.25 | Minimum mask overlap for the secondary merge (also requires bbox overlap) |
| `--min-cluster-support` | 3 | Prompt support required for a mask cluster; high-confidence smaller clusters survive |
| `--min-predicted-iou` | 0.75 | Confidence override that preserves singleton/duo clusters |
| `--prompt-batch-size` | 4 | P3-SAM prompt micro-batch size |
| `--detail-levels` | 0 | Hierarchical local passes over large under-segmented regions |
| `--cap-part-holes/--no-cap-part-holes` | on | Close boundary loops of extracted face-parts (bpy `fill_holes`) so removing a part leaves closed geometry |

Segmentation also writes `<name>_segmented_face_ids.npy` (per-face part label)
next to the segmented GLB for downstream part selection.

### High-poly meshes and segmentation proxies

P3-SAM samples a fixed point budget (up to 56k in the current presets), so
hundreds of thousands of input faces do not give the network proportionally
more information. Dense marching-cubes meshes can instead amplify cracks,
micro-islands, and jagged face-label projection.

For meshes above roughly 200k faces, use an aligned **50k–120k isotropic
remesh** as `--segmentation-proxy`. Part3D runs P3-SAM on the proxy, projects
the labels back by nearest surface plus normal agreement, then refines and
exports the original high-poly topology. Proxy and target bounds must agree
within 5%.

Prefer `text3d remesh` for this analysis proxy. A decimated LOD is suitable
only when it remains topologically connected; an LOD that inherits thousands
of marching-cubes cracks makes P3-SAM post-processing worse.

## Quantization

1. **Pre-quant DiT (quanto qint8)** — if `model-dit-qint8.*` artifacts exist under the HF cache (build with `python -m part3d.quantize_dit`).
2. **Runtime SDNQ** — otherwise, when memory-efficient or `-q int8|int4`:

| Mode | SDNQ preset | When |
|------|-------------|------|
| `auto` | `sdnq-uint8` | hw-auto memory-efficient (&lt;8 GiB) |
| `int8` | `sdnq-int8` | always (explicit) |
| `int4` | `sdnq-int4` | always (explicit) |
| `none` | — | FP16 |

## Hardware auto-detection (`--hw-auto`)

| Detected hardware | Profile |
|-------------------|---------|
| Single/multi GPU ≥ 10 GiB | FP16, no offload |
| 8–10 GiB | FP16 + CPU offload |
| &lt; 8 GiB (e.g. RTX 4050) | memory-efficient: SDNQ uint8 + offload + attention slicing |
| No CUDA | CPU + memory-efficient |

## Quality Presets

| Profile | Steps | Octree | Chunks |
|---------|-------|--------|--------|
| `fast` | 15 | 128 | 10 000 |
| `low` | 20 | 192 | 15 000 |
| `medium` | 30 | 256 | 20 000 |
| `high` | 40 | 320 | 25 000 |
| `highest` | 50 | 384 | 30 000 |

Soft-resolved via QualityEngine — explicit `--steps` / `--octree-resolution` / `--num-chunks` win.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PART3D_BIN` | Override `part3d` binary path |
| `PART3D_HW_AUTO` | `0` disables hw-auto |
| `PART3D_ALLOW_SHARED_GPU` | Override `--allow-shared-gpu` |
| `PART3D_GPU_KILL_OTHERS` | Override `--gpu-kill-others` |
| `PART3D_USE_QUANTIZED_DIT` | Force/disable pre-quant DiT load |
| `CUDA_VISIBLE_DEVICES` | Restrict visible GPUs |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator (auto-set by shared) |

## Output Layout

| File | Description |
|------|-------------|
| `{stem}_parts.glb` | Multi-geometry parts GLB |
| `{stem}_segmented.glb` | Segmented mesh with per-part colors |

## Pipeline Integration

```
Text3D (generate) → Paint3D (texture) → Part3D (decompose) → Rigging3D (auto-rig)
```

GameAssets parts stage is not wired yet (stub). UMS backend `part3d` is registered
for warm model serving. Prefer `gamedev-model-server`; `part3d decompose` accepts
`--ums-priority`, `--no-ums`, `--ums-stream` (see [`ModelServer/README.md`](../ModelServer/README.md)).

## Development

```bash
make test-part3d                # from repo root
cd Part3D && pip install -e ".[dev]" && pytest tests/
```

## License

MIT
