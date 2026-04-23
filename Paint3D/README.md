# Paint3D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

3D texturing: **Hunyuan3D-Paint 2.1** (multiview PBR in the exported GLB) + **bilateral texture smoothing** (edge-preserving, on by default) + optional **AI upscale** (Real-ESRGAN).

Uses vendored **`hy3dpaint`** under `Paint3D/src/paint3d/hy3dpaint/`; PBR weights download on demand from Hugging Face (`tencent/Hunyuan3D-2.1`, folder `hunyuan3d-paintpbr-v2-1`). See [docs/PAINT_SETUP.md](docs/PAINT_SETUP.md).

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh paint3d
```

Installs the package in `Paint3D/.venv`, PyTorch, **nvdiffrast**, downloads **Real-ESRGAN** weights when possible, and adds wrappers. See [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / advanced

```bash
cd Paint3D
pip install -e .              # core (paint)
pip install -e ".[upscale]"   # + AI upscale (spandrel)
```

The official installer handles **nvdiffrast** (`--no-build-isolation`); for manual install follow comments in `pyproject.toml`.

## CLI

```bash
# Texture mesh with reference image (GLB includes PBR material from Paint 2.1)
# Bilateral smoothing is ON by default (removes bake seam artifacts)
paint3d texture mesh.glb -i ref.png -o mesh_textured.glb

# Quality tuning
paint3d texture mesh.glb -i ref.png --bake-exp 8 --smooth-passes 3

# Skip smoothing
paint3d texture mesh.glb -i ref.png --no-smooth

# Override render/texture resolution (GPUs with >8 GB VRAM)
paint3d texture mesh.glb -i ref.png --render-size 2048 --texture-size 4096

# AI upscale (optional, requires: pip install spandrel)
paint3d texture mesh.glb -i ref.png --upscale

# Multi-GPU: split model weights across GPUs 0 and 1
paint3d texture input.glb reference.png -o output.glb --gpu-ids 0,1

# Diagnostics (rasterizer, GPU)
paint3d doctor

# Models in use
paint3d models
```

### Key options

| Option | Default | Description |
|--------|---------|-------------|
| `--bake-exp` | 6 | View blending exponent (higher = sharper seams, less color bleed) |
| `--smooth/--no-smooth` | on | Bilateral texture filter (removes bake artifacts without changing resolution) |
| `--smooth-passes` | 2 | Number of bilateral filter passes (more = smoother) |
| `--render-size` | 1024 | Back-projection rasterization resolution (higher needs more VRAM) |
| `--texture-size` | 2048 | UV atlas resolution (higher needs more VRAM) |
| `--upscale` | off | AI upscale via Real-ESRGAN (CPU, requires `spandrel`) |
| `--gpu-ids` | — | Comma-separated GPU IDs for multi-GPU weight split (e.g. `0,1`). Replaces `PAINT3D_MULTI_GPU` env var. |

## Python API

```python
from paint3d import apply_hunyuan_paint, load_mesh_trimesh

mesh = load_mesh_trimesh("model.glb")
textured = apply_hunyuan_paint(mesh, "reference.png", bake_exp=6)
```

## Dependencies

- **gamedev-shared** (GameDev monorepo — GPU, logging)
- **Hunyuan3D-2.1 `hy3dpaint`** (vendored in `src/paint3d/hy3dpaint/`) — texture pipeline; HF weights on demand
- **pymeshlab**, **xatlas**, **omegaconf**; Real-ESRGAN super-resolution is vendored (no PyPI `basicsr` / `realesrgan` packages)
- **nvdiffrast** (NVIDIA — differentiable rasterizer shim)
- **spandrel** (optional — AI upscale on exported GLB)

## Documentation

> **Deprecation:** `PAINT3D_MULTI_GPU` environment variable is deprecated. Use `--gpu-ids 0,1` instead.

- [Rasterizer setup](docs/PAINT_SETUP.md)
