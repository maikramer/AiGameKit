# Paint3D — AI 3D Texturing & PBR Painting

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

AI-powered 3D texturing with **Hunyuan3D-Paint 2.1** — generates multiview PBR materials (baseColor, normal, ORM) directly embedded in the output GLB. Includes edge-preserving **bilateral texture smoothing** to remove bake seam artifacts, and optional **AI upscaling** via Real-ESRGAN.

> Optimized for GPUs with 6 GB VRAM (RTX 4050 Laptop). SDNQ uint8 quantization and VAE tiling are applied automatically when needed.

## Overview

Paint3D is part of the [GameDev](../README.md) monorepo and sits in the asset generation pipeline between shape creation ([Text3D](../Text3D)) and downstream processing ([Part3D](../Part3D), [Rigging3D](../Rigging3D)). It uses vendored **`hy3dpaint`** from Tencent's Hunyuan3D-2.1 — model weights download on demand from Hugging Face (`tencent/Hunyuan3D-2.1`, subfolder `hunyuan3d-paintpbr-v2-1`).

**Key features:**

- Multiview PBR texturing (baseColor, normal, ORM) baked into GLB
- Bilateral texture smoothing (edge-preserving, removes seam artifacts)
- AI upscaling via Real-ESRGAN (optional, `spandrel`)
- Fast non-AI texturing: solid color or Perlin noise vertex colors
- Vertex color → PBR pipeline with [Materialize](../Materialize) integration
- Quality presets (`fast` / `low` / `medium` / `high` / `highest`)
- Multi-GPU support via `--gpu-ids`
- Low-VRAM mode for 6 GB GPUs (`--low-vram-mode`)

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh paint3d
```

Installs the package in `Paint3D/.venv`, PyTorch, **nvdiffrast**, downloads Real-ESRGAN weights when possible, and adds CLI wrappers. See [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual

```bash
cd Shared && pip install -e .
cd Paint3D && pip install -e .              # core (paint)
cd Paint3D && pip install -e ".[upscale]"   # + AI upscale (spandrel)
```

The official installer handles **nvdiffrast** (`--no-build-isolation`); for manual install follow comments in `pyproject.toml`.

> **Requirement:** CUDA GPU (NVIDIA). See [docs/PAINT_SETUP.md](docs/PAINT_SETUP.md) for rasterizer setup.

## Commands

Entry points: `paint3d` (CLI) or `python -m paint3d`.

### `paint3d texture MESH -i IMAGE`

Texturize a mesh with AI-powered PBR using Hunyuan3D-Paint 2.1. The output GLB contains embedded PBR materials.

```bash
# Basic usage
paint3d texture mesh.glb -i ref.png -o mesh_textured.glb

# Quality tuning
paint3d texture mesh.glb -i ref.png --bake-exp 8 --smooth-passes 3

# Skip smoothing
paint3d texture mesh.glb -i ref.png --no-smooth

# Override render/texture resolution (GPUs with >8 GB VRAM)
paint3d texture mesh.glb -i ref.png --render-size 2048 --texture-size 4096

# AI upscale (optional, requires: pip install spandrel)
paint3d texture mesh.glb -i ref.png --upscale --upscale-factor 2

# Multi-GPU: split model weights across GPUs 0 and 1
paint3d texture mesh.glb -i ref.png --gpu-ids 0,1

# Low VRAM mode (6 GB GPUs)
paint3d texture mesh.glb -i ref.png --low-vram-mode

# Quality preset
paint3d texture mesh.glb -i ref.png --quality high
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --image` | path | **required** | Reference image for texturing |
| `-o, --output` | path | `<mesh>_textured.glb` | Output GLB path |
| `--upscale/--no-upscale` | flag | `false` | AI upscale texture via Real-ESRGAN (requires `spandrel`) |
| `--upscale-factor` | int | `4` | Upscale factor (`2` or `4`) |
| `--max-views` | int | `6` (default) / `4` (low-vram) | Max rendering views |
| `--view-resolution` | int | `640` (default) / `384` (low-vram) | View rendering resolution (px) |
| `--render-size` | int | `2048` (default) / `1024` (low-vram) | Back-projection rasterization resolution |
| `--texture-size` | int | `4096` (default) / `2048` (low-vram) | UV atlas resolution |
| `--bake-exp` | int | `6` | Bake blending exponent (higher = sharper seams, less ghosting) |
| `--smooth/--no-smooth` | flag | `true` | Bilateral texture smoothing (removes bake artifacts) |
| `--smooth-passes` | int | `1` | Number of bilateral filter passes |
| `--low-vram-mode` | flag | `false` | SDNQ uint8 + sequential CFG chunking + ref-UNet CPU offload (with hw-auto: 6 views @ 512px, render 1536, texture 3072; without: 4 views @ 384px) |
| `--hw-auto/--no-hw-auto` | flag | `true` | Hardware auto-detection: enables low-VRAM mode on GPUs <10 GB; FP16 kept on big/multi-GPU rigs. Explicit flags win. Env kill-switch: `PAINT3D_HW_AUTO=0` |
| `--sage-attn` | flag | `false` | SageAttention (INT8 attention, Ampere+; requires `pip install sageattention`). Falls back to SDPA when unavailable |
| `--preserve-origin` | flag | `true` | Rebase mesh to AABB base at Y=0, XZ centered |
| `--allow-shared-gpu` | flag | `false` | Allow GPU with other processes |
| `--gpu-kill-others/--no-gpu-kill-others` | flag | `off` | **DEPRECATED:** terminates competing GPU processes |
| `--profile` | flag | `false` | Measure timing and VRAM |
| `--gpu-ids` | str | — | Comma-separated GPU IDs for multi-GPU weight split (e.g. `0,1`) |
| `--quality` | str | `medium` | Quality tier: `fast` / `low` / `medium` / `high` / `highest` |
| `--category` | str | — | Asset category for automatic tuning (e.g. `humanoid`, `weapon`, `prop`) |
| `-v, --verbose` | flag | `false` | Detailed logs |

### `paint3d texture-batch MANIFEST`

Batch texturing from a JSON manifest. Each item in the manifest requires `id`, `mesh`, `image`, and `output` fields. Outputs JSONL progress to stdout.

```bash
paint3d texture-batch manifest.json -O output_dir/

# Force regenerate all items
paint3d texture-batch manifest.json --force

# Multi-GPU batch
paint3d texture-batch manifest.json --gpu-ids 0,1
```

Manifest format:

```json
[
  {
    "id": "crate",
    "mesh": "models/crate.glb",
    "image": "refs/crate.png",
    "output": "output/crate_textured.glb"
  }
]
```

Accepts the same flags as `paint3d texture` (minus `-i`) plus:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-O, --output-dir` | path | `.` | Base directory for output files |
| `--force` | flag | `false` | Regenerate even if output already exists |

### `paint3d quick MESH -o OUTPUT`

Fast texturing without AI — applies solid color or Perlin noise as vertex colors. CPU-only, runs in seconds.

```bash
# Solid color
paint3d quick mesh.glb -o mesh_solid.glb --style solid --color #aa4422

# Perlin noise (stone-like)
paint3d quick mesh.glb -o mesh_perlin.glb --style perlin --tint #7a7268 --frequency 4.0
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output GLB path |
| `--style` | str | **required** | Style: `solid` or `perlin` |
| `--color` | str | `#888888` | Solid color in hex (style=solid only) |
| `--tint` | str | `#7a7268` | Base tint in hex (style=perlin only) |
| `--frequency` | float | `4.0` | Spatial noise frequency |
| `--octaves` | int | `4` | FBM layers |
| `--seed` | int | `0` | Reproducible seed |
| `--contrast` | float | `0.55` | Noise modulation strength [0–1] |
| `--preserve-origin` | flag | `true` | Rebase mesh to AABB base at Y=0, XZ centered |

### `paint3d vertex-pbr MESH -o OUTPUT`

Convert vertex colors to a full PBR material pipeline: vertex color → diffuse UV map → [Materialize](../Materialize) PBR generation → final GLB with baseColor, normal, and ORM maps.

```bash
paint3d vertex-pbr mesh.glb -o mesh_pbr.glb

# With custom preset and higher resolution
paint3d vertex-pbr mesh.glb -o mesh_pbr.glb --preset stone --texture-size 2048
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | **required** | Output GLB path with PBR materials |
| `--texture-size` | int | `1024` | Diffuse map resolution (UV unwrap + vertex color rasterization) |
| `--materialize-bin` | str | `materialize` | Materialize CLI binary path |
| `--preset` | str | `default` | PBR preset: `default`, `skin`, `floor`, `metal`, `fabric`, `wood`, `stone` |
| `-v, --verbose` | flag | `false` | Pass verbose flag to materialize |

### `paint3d doctor`

Check environment: PyTorch, CUDA, VRAM, Hunyuan3D-2.1 models, and nvdiffrast rasterizer availability.

```bash
paint3d doctor
```

### `paint3d info`

Display system information: Python version, PyTorch, CUDA, GPU details, and HuggingFace cache location.

```bash
paint3d info
```

## Quality Presets

Use `--quality <tier>` with `paint3d texture` to auto-configure rendering parameters. The QualityEngine fills defaults only for parameters the user did not explicitly set (soft resolution).

| Profile | Max Views | View Resolution | Render Size | Texture Size | Bake Exp |
|---------|-----------|----------------|-------------|--------------|----------|
| `fast` | 2 | — | — | 1024 | 6 |
| `low` | 4 | — | — | 2048 | 6 |
| `medium` | 6 | — | 2048 | 4096 | 6 |
| `high` | 8 | — | — | 4096 | 6 |
| `highest` | 10 | — | — | 4096 | 6 |

> Pair with `--category` (e.g. `humanoid`, `weapon`, `prop`) for category-specific tuning.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PAINT3D_BIN` | Override paint3d binary path |
| `PAINT3D_ALLOW_SHARED_GPU` | Allow GPU with other processes (`0`/`1`) |
| `PAINT3D_GPU_KILL_OTHERS` | Terminate competing GPU processes (`0`/`1`) |
| `PYTORCH_CUDA_ALLOC_CONF` | PyTorch CUDA memory config (auto-set by CLI) |
| `TORCHDYNAMO_DISABLE` | Disable `torch.compile` (auto-set to `1` by CLI) |

> **Deprecation:** `PAINT3D_MULTI_GPU` is deprecated. Use `--gpu-ids 0,1` instead.

## Output Layout

- **`paint3d texture`** — GLB file with embedded PBR textures (baseColor, normal, ORM) and materials.
- **`paint3d quick`** — GLB file with vertex colors baked into a solid material.
- **`paint3d vertex-pbr`** — GLB file with PBR maps generated from vertex colors via Materialize.
- **`paint3d texture-batch`** — Multiple GLB files as specified in the manifest; JSONL progress on stdout.

Default output naming: `<mesh_stem>_textured.glb` when `-o` is omitted.

## Pipeline Integration

Paint3D runs after [Text3D](../Text3D) shape generation in the GameDev asset pipeline. It produces the final textured mesh before downstream processing:

```
Text3D (shape) → Paint3D (texture) → Part3D (decomposition) → Rigging3D (rigging) → Animator3D (animation)
```

[GameAssets](../GameAssets) batch orchestrates the shape → paint flow automatically. The `gameassets batch` command generates meshes and then calls `paint3d texture` for each asset.

**Vendored code:** Do NOT modify files under `src/paint3d/hy3dpaint/` or `src/paint3d/hunyuan3d-2.1/` — these are upstream vendored code excluded from lint.

## Python API

```python
from paint3d import apply_hunyuan_paint, load_mesh_trimesh

mesh = load_mesh_trimesh("model.glb")
textured = apply_hunyuan_paint(mesh, "reference.png", bake_exp=6)
```

## Development

```bash
cd Shared && pip install -e .
cd Paint3D && pip install -e ".[dev]"
pytest tests
ruff check .
ruff format .
```

## Dependencies

- **gamedev-shared** — GPU utilities, logging, progress, quality presets
- **Hunyuan3D-2.1 `hy3dpaint`** (vendored) — texture pipeline; HF weights on demand
- **nvdiffrast** (NVIDIA) — differentiable rasterizer shim
- **pymeshlab**, **xatlas**, **omegaconf** — mesh processing
- **Real-ESRGAN** (vendored) — super-resolution without `basicsr` PyPI dependency
- **spandrel** (optional) — AI upscale on exported GLB
- **bpy** — Blender Python for mesh operations

## Documentation

- [Rasterizer setup](docs/PAINT_SETUP.md)
- [Monorepo game pipeline](../docs/MONOREPO_GAME_PIPELINE.md)

## License

MIT
