# Part3D — Semantic 3D Part Decomposition

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Semantic decomposition of 3D meshes via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentation and part generation. Optimized for ~6 GB VRAM with sequential CPU offloading, 4-bit/8-bit quantization, and `torch.compile` acceleration.

## Overview

Part3D splits a single textured mesh into semantically meaningful parts — e.g., a character into body, head, arms — using two stages:

1. **P3-SAM** — segments the mesh surface into part regions.
2. **X-Part** — generates separate 3D geometry for each detected part.

The tool auto-tunes parameters based on mesh geometry and available VRAM, or you can set them explicitly. It integrates with the [QualityEngine](../Shared/src/gamedev_shared/quality.py) preset system for cross-tool quality control.

**Requirements:**

- Python **3.10+**
- NVIDIA GPU with CUDA (~6 GB VRAM recommended; works with offloading on less)
- `torch-scatter` and `torch-cluster` (installed automatically by the official installer)

## Installation

### Official (monorepo)

From the **GameDev** repo root:

```bash
cd Shared && pip install -e .
cd Part3D && pip install -e .
```

Or use the unified installer:

```bash
./install.sh part3d
```

Equivalent: `gamedev-install part3d` (with `gamedev-shared` installed or `PYTHONPATH=Shared/src`).

> **Note:** The official installer adds `torch-scatter` and `torch-cluster` after PyTorch (see `gamedev_shared.installer.part3d_extras`).

### Manual / advanced

```bash
cd Part3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

General install docs: [docs/INSTALLING.md](../docs/INSTALLING.md).

## Commands

**Entry point:** `part3d` / `python -m part3d`

```
part3d --help
part3d decompose --help
```

### `part3d decompose MESH`

Decompose a 3D mesh into semantic parts using Hunyuan3D-Part (P3-SAM + X-Part).

```bash
# Basic decomposition — auto-tuned parameters, medium quality
part3d decompose character.glb

# Explicit output path with verbose logging
part3d decompose character.glb -o output/character_parts.glb -v

# Segment only (no part generation)
part3d decompose character.glb --segment-only

# Fast quality preset for quick preview
part3d decompose character.glb --quality fast

# Maximum quality
part3d decompose character.glb --quality highest --no-quantize-dit

# Low VRAM mode — automatic quantization + CPU offload
part3d decompose character.glb --low-vram-mode

# Multi-GPU: dispatch DiT across GPUs (only affects the DiT stage)
part3d decompose input.glb output/ --gpu-ids 0,1

# Reproducible output
part3d decompose character.glb --seed 42 --steps 25 --octree-resolution 256
```

#### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `MESH` | path | — | Input mesh file (`.glb` / `.obj`) |
| `-o, --output` | path | `{stem}_parts.glb` | Output path (decomposed parts GLB) |
| `--output-segmented` | path | `{stem}_segmented.glb` | Segmented mesh output path (colors per part) |
| `--octree-resolution` | int | auto | Octree resolution (auto-tuned by geometry/VRAM) |
| `--steps` | int | auto | DiT inference steps (auto-tuned) |
| `--num-chunks` | int | auto | Marching cubes chunks (auto-tuned) |
| `--seed` | int | None | Reproducible seed (`None` = random) |
| `--quality` | str | `medium` | Quality tier (`fast`, `low`, `medium`, `high`, `highest`) |
| `--category` | str | None | Asset category for category-specific overrides |
| `--no-auto-tune` | flag | `false` | Disable auto-tuning (uses fixed defaults) |
| `--no-cpu-offload` | flag | `false` | Disable CPU offloading (requires >10 GB VRAM) |
| `--device` | str | None | Force device (`cuda` / `cpu`) |
| `--segment-only` | flag | `false` | Segment without part generation |
| `-v, --verbose` | flag | `false` | Verbose output |
| `-q, --quantization` | str | `auto` | Quantization mode |
| `--no-quantize-dit` | flag | `false` | Skip DiT quantization (full precision) |
| `--torch-compile` / `--no-torch-compile` | flag | `false` | Enable `torch.compile` for the DiT |
| `--no-attention-slicing` | flag | `false` | Disable attention slicing |
| `--low-vram-mode` | flag | `false` | Low VRAM mode (auto quant + CPU offload + attention slicing) |
| `--profile` | flag | `false` | Enable timing, CPU, RAM, and VRAM profiling |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU DiT dispatch (e.g., `0,1`) |

## Quantization

Part3D supports multiple quantization backends to reduce VRAM usage during the DiT phase — the most memory-intensive stage.

### Modes (`--quantization` / `-q`)

| Mode | Backend | Description |
|------|---------|-------------|
| `auto` | auto-detect | Automatically selects best quantization for available VRAM **(default)** |
| `none` | — | Full precision (FP32/FP16) — highest quality, most VRAM |
| `int8` | bitsandbytes | 8-bit quantization — good quality/VRAM balance |
| `int4` | bitsandbytes | 4-bit quantization — lowest VRAM, some quality loss |

### DiT quantization control (`--no-quantize-dit`)

The DiT (Diffusion Transformer) is the single most VRAM-heavy component. By default, Part3D applies quantization to the DiT to keep peak memory manageable. Use `--no-quantize-dit` to disable this optimization when maximum precision is needed:

```bash
# Maximum quality, no DiT quantization (requires more VRAM)
part3d decompose model.glb --quality high --no-quantize-dit
```

### Low VRAM mode (`--low-vram-mode`)

Enables a bundle of optimizations: auto quantization (`-q auto`), CPU offloading, and attention slicing. Ideal for GPUs with <8 GB VRAM:

```bash
part3d decompose model.glb --low-vram-mode
```

## Quality Presets

The `--quality` flag controls DiT steps, octree resolution, and chunk count via the shared [QualityEngine](../Shared/src/gamedev_shared/quality.py). Values are soft-resolved — explicitly passing `--steps` or `--octree-resolution` overrides the preset for that parameter.

| Profile | Steps | Octree Resolution | Chunks |
|---------|-------|-------------------|--------|
| `fast` | 12 | 128 | 4,096 |
| `low` | 18 | 192 | 6,000 |
| `medium` | 25 | 256 | 8,000 |
| `high` | 30 | 384 | 20,000 |
| `highest` | 40 | 512 | 30,000 |

Combine with `--category` for asset-type-specific overrides (e.g., `humanoid`, `weapon`, `prop`).

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PART3D_BIN` | Override `part3d` binary path (used by GameAssets batch and other tools) |
| `CUDA_VISIBLE_DEVICES` | Restrict visible GPUs (set before execution) |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA memory allocator config (auto-set by `gamedev-shared`) |

## Output Layout

Running `part3d decompose input.glb` produces:

| File | Description |
|------|-------------|
| `{stem}_parts.glb` | Decomposed parts mesh (multi-geometry GLB) |
| `{stem}_segmented.glb` | Segmented mesh with per-part vertex colors (visualization) |

If no parts are detected, the pipeline falls back to segment-only mode and writes an empty placeholder `_parts.glb` to avoid breaking downstream tools.

Use `-o` / `--output` to customize the parts path and `--output-segmented` for the segmented mesh.

## Pipeline Integration

Part3D fits into the GameDev asset pipeline between texturing and rigging:

```
Text3D (generate) → Paint3D (texture) → Part3D (decompose) → Rigging3D (auto-rig)
```

- **GameAssets batch** auto-detects when parts are needed from the manifest columns and `game.yaml` profile blocks. Use `--no-parts` to opt out.
- **Rigging3D** prefers `_parts.glb` as input when available, allowing per-part weight painting.
- Part3D is excluded from CI (heavy PyTorch/diffusers deps, not viable on GPU-less runners). Run tests locally.

## Development

```bash
cd Part3D
pip install -e ".[dev]"        # Install with dev dependencies
pytest tests/                   # Run tests
ruff check .                    # Lint
ruff format .                   # Format
ruff format --check .           # Check formatting
```

Run from the repo root:

```bash
make test-part3d                # pytest Part3D only
make lint                       # ruff check all Python packages
make fmt                        # ruff format all Python packages
```

## License

MIT
