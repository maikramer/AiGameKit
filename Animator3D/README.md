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
animator3d game-pack rigged.glb animated.glb --preset humanoid
```

| Command | Description |
|---------|-------------|
| **Utility** | |
| `check` | Verifies `bpy` and prints Blender version |
| `inspect` | Imports and lists armatures, bone sample, actions |
| `export` | Re-exports (import/export roundtrip test) |
| `list-clips` | JSON list of animation clips in a file |
| `screenshot` | Multi-view PNG renders (Workbench default; `--engine eevee`, `--ortho`, `--no-transparent-film`) |
| `inspect-rig` | Rig views with bones visible; optional weight heatmap (`--show-weights`) |
| **Animation** | |
| `wave-idle` | Test animation (oscillation on one bone) |
| `breathe-idle` | Multi-bone idle (breathing, wings, tail, neck, arms) |
| `attack` | Strike/bite animation |
| `walk` | Walk cycle with leg alternation and arm counter-swing |
| `run` | Run cycle (faster cadence, larger amplitude) |
| `jump` | Jump: crouch → extend → airborne → land (non-looping) |
| `fall` | Falling pose with wind sway (non-looping) |
| `hover` | Hovering (wing flapping) |
| `soar` | Soaring/gliding |
| `dive` | Dive attack |
| `fire` | Fire breath |
| `land` | Landing |
| `roar` | Victory roar |
| **Texture** | |
| `texture-project` | Project textures from an original mesh onto Part3D part meshes (Cycles bake, diffuse color) |
| **Batch** | |
| `game-pack` | Generate all game animations in one command (`--preset humanoid` / `creature` / `flying`; optional `--clips` filter) |

### `game-pack`

Generates every clip defined by a preset in a single pass. Presets:

- **`humanoid`** — idle, walk, run, jump, fall
- **`creature`** — idle, walk, attack, roar
- **`flying`** — idle, hover, soar, dive, land

Use `--clips` with a comma-separated list of short names (matched against clip names in the GLB, e.g. `idle`, `walk`, `run`) to run only a subset of the preset.

```bash
animator3d game-pack rigged.glb animated.glb --preset humanoid
animator3d game-pack dragon.glb dragon_anim.glb --preset flying
animator3d game-pack monster.glb monster_anim.glb --preset creature
animator3d game-pack hero.glb hero_anim.glb --preset humanoid --clips idle,walk,run
```

### `texture-project`

Projects textures from a source mesh onto Part3D decomposed parts using Blender Cycles bake (selected-to-active, diffuse color only). Useful after `part3d decompose` when parts lose the original texture.

```bash
animator3d texture-project original_textured.glb parts.glb -o parts_textured.glb
animator3d texture-project original.glb parts.glb -o out.glb --resolution 2048 --margin 16
```

| Option | Default | Description |
|--------|---------|-------------|
| `-o` / `--output` | `<input>_textured.glb` | Output GLB path |
| `--resolution` | `1024` | Bake texture resolution (px) |
| `--margin` | `16` | Bake margin in pixels (extends UV islands) |

Automatically invoked by `gameassets batch` when Part3D is enabled and `animator3d` is available.

## Flow with Rigging3D

1. `rigging3d pipeline --input mesh.glb --output rigged.glb`
2. `animator3d wave-idle rigged.glb with_animation.glb` (or your own Python pipeline using `animator3d.bpy_ops`)

## License

MIT — see [`LICENSE`](LICENSE).
