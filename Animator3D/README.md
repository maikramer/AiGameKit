# Animator3D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

**3D animation** CLI using the [Blender Python API](https://docs.blender.org/api/current/) (`bpy`), designed to follow **Rigging3D** (rigged mesh → keyframes → GLB/FBX export).

## Requirements

- **Python 3.13** — the PyPI wheel `bpy==5.1.0` requires 3.13 and matches **Blender 5.1**.
- Blender embedded in the `bpy` package (no window; background execution).

## Installation

### Official (monorepo)

At the **GameDev** repo root (folder with `install.sh` and `Shared/`):

```bash
cd /path/to/GameDev
./install.sh animator3d
```

Equivalent: `gamedev-install animator3d`. General docs: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development

```bash
cd Animator3D
python3.13 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Usage

```bash
animator3d check
animator3d inspect rigged.glb
animator3d inspect rigged.glb --json-out
animator3d export rigged.glb copy.glb
animator3d wave-idle rigged.glb animated.glb --frames 60
animator3d wave-idle rigged.glb animated.glb --bone mixamorig:Spine
```

| Command | Description |
|---------|-------------|
| `check` | Verifies `bpy` and prints Blender version |
| `inspect` | Imports and lists armatures, bone sample, actions |
| `screenshot` | Multi-view PNG renders (Workbench default; `--engine eevee`, `--ortho`, `--no-transparent-film`) |
| `inspect-rig` | Rig views with bones visible; optional weight heatmap (`--show-weights`) |
| `export` | Re-exports (import/export roundtrip test) |
| `wave-idle` | Test animation (oscillation on one bone) and export |

## Flow with Rigging3D

1. `rigging3d pipeline --input mesh.glb --output rigged.glb`
2. `animator3d wave-idle rigged.glb with_animation.glb` (or your own Python pipeline using `animator3d.bpy_ops`)

## License

MIT — see [`LICENSE`](LICENSE).
