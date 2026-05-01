# Rigging3D — Auto-Rigging for 3D Models

> Automated skeleton generation, skinning, and merge pipeline for 3D meshes, powered by [UniRig](https://github.com/VAST-AI-Research/UniRig) (MIT). Turns a static GLB/OBJ into a fully rigged model ready for animation.

**Version:** 0.5.0 · **Language:** Python 3.11 · **License:** MIT

---

## Overview

Rigging3D is a CLI tool that automates the three-stage rigging pipeline:

1. **Skeleton** — Generates an armature (bone hierarchy) from the input mesh using UniRig's skeleton inference.
2. **Skin** — Predicts per-vertex skinning weights that bind the mesh to the skeleton.
3. **Merge** — Combines the skinned result with the original mesh geometry, producing a rigged GLB.

The `pipeline` command chains all three stages into a single invocation. Individual commands (`skeleton`, `skin`, `merge`) are available for finer control.

Typical use-case: take a static 3D character from Text3D/GameAssets and produce a rigged GLB that Animator3D can animate with clip commands (`run`, `jump`, `fall`).

---

## Installation

### Prerequisites

- **Python 3.11** — required because `bpy==5.0.1` (PyPI) only ships for `cp311`, and Open3D has no stable wheels for Python 3.13.
- **NVIDIA GPU with CUDA** — ≥6–8 GB VRAM depending on mesh density.
- **bash** — required for inference scripts (on Windows: Git Bash or MSYS2).
- **UniRig model weights** — download automatically from [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) on first run.

> **Why not `bpy` 5.1?** On PyPI, `bpy==5.1.0` exists only for Python 3.13, while Open3D used by UniRig has no stable wheels for 3.13. Rigging3D pins `bpy==5.0.1` on Python 3.11. For `bpy==5.1.0` (Blender 5.1), use the [Animator3D](../Animator3D/) project with Python 3.13.

### Official installer (monorepo)

```bash
cd /path/to/GameDev
./install.sh rigging3d
```

Installs the full inference stack (PyTorch CUDA, `bpy`, Open3D, spconv, PyG, etc.). See [docs/INSTALLING.md](../docs/INSTALLING.md) for details.

### Setup script

```bash
cd Rigging3D
bash scripts/setup.sh              # auto-detects CUDA driver
bash scripts/setup.sh --python python3.11   # specify interpreter
bash scripts/setup.sh --force       # recreate venv from scratch
```

### Manual install

```bash
# Shared first (required dependency)
cd Shared && pip install -e .

# Rigging3D with full inference deps
cd Rigging3D && python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[inference]"
```

### CUDA-specific dependencies

The setup script installs these automatically. If installing manually:

```bash
# torch-scatter + torch-cluster (adjust torch/CUDA versions to match your venv)
pip install torch-scatter torch-cluster -f https://data.pyg.org/whl/torch-2.11.0+cu130.html

# spconv + cumm (cu121 for CUDA 12.x/13.x)
pip install cumm-cu121 spconv-cu121
```

> **Note:** The pipeline uses PyTorch native SDPA (`torch.nn.functional.scaled_dot_product_attention`) — the `flash-attn` package is **not** required.

> **If PyTorch ends up CPU-only** (no CUDA line from `nvidia-smi`): set `RIGGING3D_FORCE_CUDA=1` and re-run the installer with `--inference`.

---

## Global Flags

These flags apply to all `rigging3d` subcommands.

```bash
rigging3d [GLOBAL_FLAGS] <COMMAND> [COMMAND_FLAGS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--root` | path | Package bundled | UniRig inference tree (`configs/` + `src/`). Overrides `RIGGING3D_ROOT`. |
| `--python` | str | Current interpreter | Python interpreter path (conda/venv). Overrides `RIGGING3D_PYTHON`. |
| `--profiler` | flag | `false` | Enable performance profiling (writes to perf DB). |
| `--gpu-ids` | str | None | GPU IDs for subprocesses (e.g., `"0,1"`). Propagates `CUDA_VISIBLE_DEVICES`. |
| `--version` | — | — | Show version and exit. |

---

## Commands

### `rigging3d pipeline`

Full pipeline: skeleton → skin → merge → rigged GLB. This is the recommended command for most use cases.

```bash
rigging3d pipeline -i character.glb -o character_rigged.glb

# With reproducible seed and quality preset
rigging3d pipeline -i character.glb -o character_rigged.glb --seed 42 --quality high

# Low VRAM mode (reduces num_train_vertex to 256)
rigging3d pipeline -i character.glb -o character_rigged.glb --low-vram

# Keep intermediate files for debugging
rigging3d pipeline -i character.glb -o character_rigged.glb --work-dir ./debug --keep-temp

# Multi-GPU: skeleton on GPU 0, skin on GPU 1
rigging3d --gpu-ids 0,1 pipeline -i character.glb -o character_rigged.glb
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --input` | path | **required** | Input mesh (GLB/OBJ). |
| `-o, --output` | path | **required** | Output rigged GLB. |
| `--work-dir` | path | temp dir | Working directory for intermediate files. |
| `--seed` | int | None | Reproducible seed (None = random). |
| `--keep-temp` | flag | `false` | Keep temporary working directory. |
| `--smooth-iterations` | int | 3 | Laplacian smoothing passes during merge. |
| `--groups-per-vertex` | int | 8 | Maximum bone influences per vertex. |
| `--no-prep` | flag | `false` | Skip mesh preparation (remesh/repair). |
| `--low-vram` | flag | `false` | Low VRAM mode (`num_train_vertex=256`). |
| `--draco` | flag | `false` | Apply Draco compression on output GLB. |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest`. |

**Mesh preparation** (unless `--no-prep`): before rigging, the input mesh is cleaned — vertices merged, degenerate faces removed, non-manifold edges repaired, holes closed, and isotropic remeshing applied. If preparation fails, the original mesh is used with a warning.

**Origin validation**: after merge, the pipeline warns if the model's base is far from Y≈0, indicating the mesh origin may not be at the feet. Regenerate with `text3d reorigin-feet` to fix.

---

### `rigging3d skeleton`

Generate an armature (bone hierarchy) from a mesh using UniRig. Output is a GLB with embedded skeleton (`.fbx` also supported).

```bash
rigging3d skeleton -i character.glb -o skeleton.glb
rigging3d skeleton --seed 42 -i character.glb -o skeleton.glb

# Batch mode
rigging3d skeleton --input-dir ./meshes/ --output-dir ./skeletons/
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --input` | path | None | Input mesh. |
| `-o, --output` | path | None | Output skeleton GLB. |
| `--seed` | int | None | Reproducible seed. |
| `--skeleton-task` | str | `configs/task/quick_inference_skeleton_articulationxl_ar_256.yaml` | Skeleton task config YAML. |
| `--input-dir` | path | None | Input directory (batch mode; requires `--output-dir`). |
| `--output-dir` | path | None | Output directory (batch mode). |

---

### `rigging3d skin`

Predict skinning weights for a mesh+ skeleton using UniRig.

```bash
rigging3d skin -i skeleton.glb -o skinned.glb
rigging3d skin --seed 42 -i skeleton.glb -o skinned.glb

# Batch mode
rigging3d skin --input-dir ./skeletons/ --output-dir ./skinned/
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --input` | path | None | Input mesh with skeleton. |
| `-o, --output` | path | None | Output skinned mesh. |
| `--seed` | int | None | Reproducible seed. |
| `--skin-task` | str | `configs/task/quick_inference_unirig_skin.yaml` | Skin task config YAML. |
| `--input-dir` | path | None | Input directory (batch mode; requires `--output-dir`). |
| `--output-dir` | path | None | Output directory (batch mode). |
| `--data-name` | str | `raw_data.npz` | Data name for intermediate NPZ. |

---

### `rigging3d merge`

Merge the skinned result with the original mesh geometry, producing a final rigged GLB.

```bash
rigging3d merge -s skinned.glb -t original.glb -o rigged.glb
rigging3d merge -s skinned.glb -t original.glb -o rigged.glb --smooth-iterations 5 --draco
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-s, --source` | path | **required** | Skinned mesh (output of `skin`). |
| `-t, --target` | path | **required** | Original mesh (pre-rigging geometry). |
| `-o, --output` | path | **required** | Output rigged GLB path. |
| `--require-suffix` | str | `obj,fbx,FBX,dae,glb,gltf,vrm` | Accepted file extensions. |
| `--smooth-iterations` | int | 3 | Laplacian smoothing passes. |
| `--groups-per-vertex` | int | 8 | Maximum bone influences per vertex. |
| `--draco` | flag | `false` | Apply Draco compression on output. |

---

## Quality Presets

Rigging3D integrates with the monorepo's [QualityEngine](../Shared/src/gamedev_shared/quality/) for soft parameter resolution. The `--quality` flag on `pipeline` fills defaults for `smooth-iterations`, `groups-per-vertex`, and `low-vram` when the user hasn't explicitly set them.

```bash
rigging3d pipeline -i mesh.glb -o rigged.glb --quality high
```

| Tier | Behavior |
|------|----------|
| `fast` | Minimal smoothing, fewer vertex groups. Best for prototyping. |
| `low` | Reduced quality for faster processing. |
| `medium` | Default balanced preset. |
| `high` | More smoothing passes, more vertex groups per vertex. |
| `highest` | Maximum quality settings. |

User-specified flags always take precedence over quality preset defaults.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `RIGGING3D_ROOT` | Path to UniRig inference tree (fallback if `--root` is not set). |
| `RIGGING3D_PYTHON` | Python interpreter path (fallback if `--python` is not set). |
| `RIGGING3D_SMOOTH_ITERATIONS` | Override smoothing iterations in merge (used internally). |
| `RIGGING3D_GROUPS_PER_VERTEX` | Override vertex group limit in merge (used internally). |
| `RIGGING3D_DRACO` | Enable/disable Draco compression in merge (`1`/`0`, used internally). |
| `RIGGING3D_FORCE_CUDA` | Force PyTorch to install CUDA variant during setup. |
| `RIGGING3D_PYTORCH_CUDA_INDEX` | Alternative CUDA wheel index for PyTorch. |
| `CUDA_VISIBLE_DEVICES` | GPU visibility (propagated automatically when `--gpu-ids` is set). |

---

## Output Layout

```
character.glb          # Input: static mesh
character_rigged.glb   # Output: rigged GLB with armature + skin weights
```

When using `pipeline` with `--work-dir`, intermediate files are created inside the work directory:

```
work-dir/
  _prepped.glb         # Mesh after preparation (remesh/repair)
  _skeleton.glb        # Generated skeleton
  _skin.glb            # Skinned intermediate
```

These are cleaned up automatically unless `--keep-temp` is passed.

---

## Pipeline Integration

Rigging3D fits into the monorepo asset pipeline as follows:

```
Text3D / Paint3D  →  Part3D (optional)  →  Rigging3D  →  Animator3D
     │                     │                    │              │
  static GLB          _parts.glb          _rigged.glb     animated GLB
```

- **Input preference:** When a `_parts.glb` exists (from Part3D decomposition), the pipeline uses it as input; otherwise falls back to the base mesh.
- **GameAssets batch:** `gameassets batch` orchestrates the full flow automatically, propagating `--gpu-ids` and `CUDA_VISIBLE_DEVICES` to Rigging3D sub-processes.
- **Animator3D:** The rigged output feeds into Animator3D's `game-pack` command for animation clip generation.

---

## Development

```bash
# Install with dev dependencies
cd Shared && pip install -e .
cd Rigging3D && pip install -e ".[dev]"

# Run tests
pytest tests

# Lint and format
ruff check .
ruff format .

# Type checking (runs on Shared/src)
make typecheck
```

The vendored UniRig code in `src/rigging3d/unirig/` is excluded from linting (ruff).

---

## License

- **Rigging3D CLI:** MIT — [`LICENSE`](LICENSE)
- **UniRig code:** MIT — [`unirig/LICENSE`](src/rigging3d/unirig/LICENSE) · [`THIRD_PARTY.md`](THIRD_PARTY.md)
- **HuggingFace weights:** the [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) repository card contains licensing terms — review before use.
