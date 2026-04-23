# Part3D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Semantic decomposition of 3D meshes via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentation and part generation.

## Requirements

- Python **3.10+**
- NVIDIA GPU with CUDA recommended (~5 GB VRAM peak with offloading; see CLI)
- Tool registration: [`Shared/src/gamedev_shared/installer/registry.py`](../Shared/src/gamedev_shared/installer/registry.py)

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh part3d
```

Equivalent: `gamedev-install part3d` (with `gamedev-shared` installed or `PYTHONPATH=Shared/src`).

### Manual / advanced

```bash
cd Part3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Note: the official installer adds **torch-scatter** and **torch-cluster** after PyTorch (see `gamedev_shared.installer.part3d_extras`).

### Local shortcut

```bash
cd Part3D
python3 scripts/installer.py
```

## Usage

```bash
part3d --help
part3d decompose mesh.glb -o parts.glb -v

# Multi-GPU: dispatch DiT across GPUs (only affects the DiT stage)
part3d decompose input.glb output/ --gpu-ids 0,1
```

General install docs: [docs/INSTALLING.md](../docs/INSTALLING.md).
