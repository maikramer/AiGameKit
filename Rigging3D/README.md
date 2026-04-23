# Rigging3D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

**3D auto-rigging** CLI based on [UniRig](https://github.com/VAST-AI-Research/UniRig) (MIT).

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh rigging3d
```

This **always** installs the full inference stack (PyTorch CUDA, `bpy`, Open3D, spconv, PyG, etc.) — same behavior as `gamedev_shared.installer.unified`. Guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development (`scripts/setup.sh`)

One command in the project directory: venv, PyTorch+CUDA, inference deps, spconv, torch-scatter/cluster.

```bash
cd Rigging3D
bash scripts/setup.sh
```

The script auto-detects the driver CUDA version. Requires **Python 3.11** (PyPI wheels `bpy==5.0.1` and **Open3D**; see Blender 5.1 note below).

```bash
bash scripts/setup.sh --python python3.11    # specify interpreter
bash scripts/setup.sh --force                # recreate venv from scratch
```

**Note:** the pipeline uses PyTorch `torch.nn.functional.scaled_dot_product_attention` (SDPA) — the `flash-attn` package is **not** required.

### Local shortcut (`scripts/installer.py`)

- **`./install.sh rigging3d`** (at repo root) is equivalent to **`python3 scripts/installer.py --inference`** in this folder (full inference).
- **Without** `--inference`: only `pip install -e` + wrappers; the summary explains how to finish (useful for minimal CI).

```bash
cd Rigging3D
python3 scripts/installer.py --inference
```

### Manual (step by step)

```bash
cd Rigging3D && python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[inference]"
```

**Windows:** full inference was tested on **Linux**; on Windows use `python scripts/installer.py --inference` (default Python `python` if `PYTHON_CMD` is unset).

**If PyTorch ends up CPU-only** (e.g. `nvidia-smi` shows no “CUDA Version” line from NVML/driver): set `RIGGING3D_FORCE_CUDA=1` and re-run the installer with `--inference`, or use `bash scripts/setup.sh` which applies the same logic. Optional: `RIGGING3D_PYTORCH_CUDA_INDEX` for another CUDA wheel index.

### CUDA-specific deps (if you installed manually)

`setup.sh` installs everything automatically, but if you install manually, use the same PyG URL as the script (depends on `torch` and CUDA runtime). With **Python 3.11**, confirm a `torch-*+cu*` wheel exists for your combo; otherwise `setup.sh` may try building from source.

```bash
# torch-scatter + torch-cluster (adjust torch and CUDA to your venv):
pip install torch-scatter torch-cluster -f https://data.pyg.org/whl/torch-2.11.0+cu130.html

# spconv + cumm (cu121 for CUDA 12.x and 13.x):
pip install cumm-cu121 spconv-cu121
```

### Model weights

HF weights download automatically on first run: [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig). Confirm terms on the card (see [GameDev/README](../README.md)).

## Requirements

- Python **3.11** (range supported by inference `pyproject.toml`; `bpy` 5.0.1 on PyPI)
- NVIDIA GPU with CUDA (≥6–8 GB VRAM depending on mesh; smaller GPUs may fail on very dense meshes)
- **bash** for inference scripts — on Windows: Git Bash or MSYS2

### Blender 5.1.0, `bpy`, and Open3D

- On **PyPI**, the **`bpy==5.1.0`** wheel (aligned with Blender **5.1.0**) exists only for **Python 3.13**.
- **Open3D** used by UniRig does **not** publish stable wheels for **Python 3.13** (only up to `cp312` at current release).
- Therefore Rigging3D keeps **`bpy==5.0.1`** on **Python 3.11** for full inference (mesh + merge with Open3D). The API is Blender **5.0** line, close to 5.1 for most `bpy.ops` used in the pipeline.
- For **`bpy==5.1.0`** matching your Blender 5.1.0, use the [**Animator3D**](../Animator3D/) project with **Python 3.13** (animation/export only, no Open3D in the same venv).

## Usage

```bash
rigging3d pipeline --input mesh.glb --output rigged.glb
rigging3d skeleton --input mesh.glb --output skel.glb
rigging3d skin    --input skel.glb --output skin.glb
rigging3d merge   --source skin.glb --target mesh.glb --output rigged.glb

# Multi-GPU: propagate CUDA_VISIBLE_DEVICES to subprocesses (skeleton, skin, merge)
rigging3d --gpu-ids 0,1 pipeline --input mesh.glb --output rigged.glb
```

To point at another inference tree:

```bash
export RIGGING3D_ROOT=/other/path
```

## Commands

| Command | Description |
|---------|-------------|
| `skeleton` | Generate skeleton (GLB; `.fbx` still supported) |
| `skin` | Skinning weights |
| `merge` | Merge skin + original mesh |
| `pipeline` | skeleton → skin → merge |

## License

- Rigging3D (CLI): **MIT** — [`LICENSE`](LICENSE)
- UniRig code: **MIT** — [`unirig/LICENSE`](src/rigging3d/unirig/LICENSE) · [`THIRD_PARTY.md`](THIRD_PARTY.md)
- **HF weights:** the [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) repo may not include `LICENSE` at root; validate terms on the card and explicit forks if needed. Table in [monorepo README](../README.md).
