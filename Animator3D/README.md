# Animator3D — 3D Animation & Game-Pack

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Procedural animation CLI powered by the [Blender Python API](https://docs.blender.org/api/current/) (`bpy 5.1.0`).
Generates keyed animation clips for rigged GLB models — walk cycles, combat, flight, idle, and more —
then exports a single animated GLB ready for game engines. Designed as the **final stage** after
[Rigging3D](../Rigging3D/) in the GameDev asset pipeline.

## Overview

| Feature | Details |
|---------|---------|
| **Input** | Rigged GLB (from Rigging3D / UniRig) |
| **Output** | Animated GLB with embedded named clips (NLA actions) |
| **Runtime** | Blender 5.1 embedded via `bpy==5.1.0` (headless, no GUI) |
| **Python** | 3.13+ (required by `bpy 5.1.0` PyPI wheels) |
| **GPU** | Not required for animation; only needed for upstream tools (Text3D, Paint3D) |
| **Entry point** | `animator3d` (CLI) or `python -m animator3d` |

Animator3D classifies armature bones automatically (legs, wings, tail, neck, spine, arms, head) and
applies procedural keyframes matched to each chain. Bones are renamed from chain analysis via
`rename_bones_from_chains` before animation, so it works with arbitrary rigs — Mixamo, UniRig,
Auto-Rig Pro, etc.

## Installation

### Monorepo (recommended)

```bash
# From the GameDev repo root:
./install.sh animator3d
# Equivalent: python3 -m gamedev_shared.installer.unified animator3d
```

### Manual / development

```bash
# Install Shared first (required dependency):
cd Shared && pip install -e .

# Then Animator3D with dev dependencies:
cd Animator3D
python3.13 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

**Requirements:** Python 3.13, `bpy==5.1.0` (Blender 5.1 wheel), `gamedev-shared`, `click`,
`rich`, `rich-click`.

## Commands

All commands share the `animator3d` entry point. Run `animator3d --help` or
`animator3d <command> --help` for usage.

### `animator3d game-pack`

Generate all animation clips for a preset in one command. This is the primary command used by
[GameAssets](../GameAssets/) batch processing.

```bash
# Humanoid hero — walk, run, jump, fall, breathe-idle:
animator3d game-pack rigged.glb animated.glb --preset humanoid

# Dragon — breathe-idle, walk, attack, roar:
animator3d game-pack dragon.glb dragon_anim.glb --preset creature

# Flying beast — breathe-idle, hover, soar, dive, land:
animator3d game-pack griffin.glb griffin_anim.glb --preset flying

# Only specific clips from a preset:
animator3d game-pack hero.glb hero_anim.glb --preset humanoid --clips walk,run,jump

# With Draco compression for smaller file size:
animator3d game-pack hero.glb hero_anim.glb --preset humanoid --draco
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `INPUT` | path | **required** | Input rigged GLB path |
| `OUTPUT` | path | **required** | Output animated GLB path |
| `--preset` | choice | `humanoid` | Preset: `humanoid`, `creature`, `flying` |
| `--clips` | str | (all) | Filter clips by comma-separated names (e.g. `walk,run`) |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

**Preset clip lists:**

| Preset | Clips Generated |
|--------|----------------|
| `humanoid` | breathe-idle, walk, run, jump, fall |
| `creature` | breathe-idle, walk, attack, roar |
| `flying` | breathe-idle, hover, soar, dive, land |

---

### Humanoid Animations

#### `animator3d walk INPUT OUTPUT`

Walk cycle with alternating leg movement, trunk sway, and tail counter-swing.

```bash
animator3d walk rigged.glb animated.glb
animator3d walk rigged.glb animated.glb --frames 60 --cycles 3.0 --leg-amp 0.18
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `48` | Total animation frames |
| `--cycles` | float | `2.0` | Walk cycles within the frame range |
| `--leg-amp` | float | `0.14` | Leg swing amplitude (radians) |
| `--clip-name` | str | `Animator3D_Walk` | Custom clip name (max 64 chars) |
| `--append/--no-append` | flag | `true` | Keep existing clips and append (`--no-append` clears first) |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d run INPUT OUTPUT`

Run cycle — faster cadence, larger amplitude, arm counter-swing.

```bash
animator3d run rigged.glb animated.glb --frames 24 --leg-amp 0.30
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `36` | Total animation frames |
| `--cycles` | float | `2.0` | Run cycles within the frame range |
| `--leg-amp` | float | `0.22` | Leg swing amplitude (radians) |
| `--clip-name` | str | `Animator3D_Run` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d jump INPUT OUTPUT`

Non-looping jump: crouch → extend → airborne → land.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `36` | Total animation frames |
| `--clip-name` | str | `Animator3D_Jump` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d fall INPUT OUTPUT`

Non-looping fall: arms open, wind sway.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `24` | Total animation frames |
| `--clip-name` | str | `Animator3D_Fall` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d attack INPUT OUTPUT`

Strike / bite animation: torso and neck lunge forward, wings sweep, tail counterbalance. Legs stay
planted.

```bash
animator3d attack dragon.glb dragon_anim.glb --frames 72 --strikes 3
animator3d attack dragon.glb dragon_anim.glb --wing-amp 0.80 --neck-amp 0.70
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `72` | Total animation frames |
| `--strikes` | int | `1` | Number of strikes per clip |
| `--wing-amp` | float | `0.62` | Wing sweep amplitude (radians) |
| `--neck-amp` | float | `0.55` | Neck / bite amplitude (radians) |
| `--tail-amp` | float | `0.42` | Tail counterbalance amplitude (radians) |
| `--clip-name` | str | `Animator3D_Attack` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d wave-idle INPUT OUTPUT`

Test oscillation animation on a single bone. Useful for verifying rig connectivity.

```bash
animator3d wave-idle rigged.glb animated.glb --bone mixamorig:Spine
animator3d wave-idle rigged.glb animated.glb --frames 90 --clip-name TestOscillation
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `60` | Total animation frames |
| `--bone` | str | (auto) | Target bone name (auto-detects if omitted) |
| `--clip-name` | str | `Animator3D_WaveIdle` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d breathe-idle INPUT OUTPUT`

Multi-bone idle animation: breathing (spine/chest), wing micro-movement, tail sway, neck drift.
Automatically classifies bones and applies appropriate motion to each chain.

```bash
animator3d breathe-idle dragon.glb dragon_anim.glb --frames 120
animator3d breathe-idle dragon.glb dragon_anim.glb --wing-amp 0.40 --tail-amp 0.25 --neck-amp 0.20
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `120` | Total animation frames |
| `--cycles` | float | `2.0` | Breathing cycles within the frame range |
| `--wing-amp` | float | `0.25` | Wing micro-movement amplitude (radians) |
| `--tail-amp` | float | `0.15` | Tail sway amplitude (radians) |
| `--neck-amp` | float | `0.10` | Neck drift amplitude (radians) |
| `--clip-name` | str | `Animator3D_BreatheIdle` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

---

### Creature / Flying Animations

#### `animator3d hover INPUT OUTPUT`

Fast wing flap while hovering in place. Trunk stays stable.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `60` | Total animation frames |
| `--cycles` | float | `3.5` | Wing flap cycles |
| `--wing-amp` | float | `0.38` | Wing flap amplitude (radians) |
| `--clip-name` | str | `Animator3D_Hover` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d soar INPUT OUTPUT`

Majestic soaring with slow, wide wing beats and tail steering.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `90` | Total animation frames |
| `--cycles` | float | `1.5` | Wing beat cycles |
| `--clip-name` | str | `Animator3D_Soar` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d dive INPUT OUTPUT`

Dive attack: wings fold, rapid descent, sharp impact.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `48` | Total animation frames |
| `--clip-name` | str | `Animator3D_DiveAttack` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d fire INPUT OUTPUT`

Fire breath: chest expands, neck lunges forward, powerful burst bursts.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `64` | Total animation frames |
| `--bursts` | int | `2` | Number of fire bursts |
| `--clip-name` | str | `Animator3D_FireBreath` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d land INPUT OUTPUT`

Majestic landing: controlled descent, aerodynamic braking, gentle impact.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `80` | Total animation frames |
| `--clip-name` | str | `Animator3D_Land` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d roar INPUT OUTPUT`

Victory roar: chest inflated, head raised, majestic pose.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--frames` | int | `96` | Total animation frames |
| `--clip-name` | str | `Animator3D_VictoryRoar` | Custom clip name |
| `--append/--no-append` | flag | `true` | Append to existing clips |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

---

### Utility Commands

#### `animator3d check`

Verify the `bpy` runtime is functional. Prints Blender version and scene FPS.

```bash
animator3d check
```

#### `animator3d inspect INPUT`

Import a GLB/GLTF/FBX and list armatures, bone sample, and actions.

```bash
animator3d inspect rigged.glb
animator3d inspect rigged.glb --json-out
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `INPUT` | path | **required** | GLB/GLTF/FBX to inspect |
| `--json-out` | flag | `false` | Output as JSON to stdout |

#### `animator3d list-clips INPUT`

List animation clips (NLA actions) as JSON — useful for pipeline scripts.

```bash
animator3d list-clips animated.glb
```

#### `animator3d export INPUT OUTPUT`

Re-export a GLB (import/export roundtrip). Useful for validation.

```bash
animator3d export rigged.glb copy.glb
animator3d export rigged.glb compressed.glb --draco
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `INPUT` | path | **required** | Input file path |
| `OUTPUT` | path | **required** | Output file path |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

#### `animator3d texture-project ORIGINAL PARTS -o OUTPUT`

Project textures from an original mesh onto Part3D decomposed parts using Blender Cycles bake
(selected-to-active, diffuse color). Useful after `part3d decompose` when parts lose the original
texture.

```bash
animator3d texture-project original_textured.glb parts.glb -o parts_textured.glb
animator3d texture-project original.glb parts.glb -o out.glb --resolution 2048 --margin 16
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `ORIGINAL` | path | **required** | Original textured GLB |
| `PARTS` | path | **required** | Parts GLB (from Part3D) |
| `-o, --output` | path | **required** | Output textured parts GLB |
| `--resolution` | int | `1024` | Bake texture resolution (px, square) |
| `--margin` | int | `16` | UV island margin in pixels |
| `--draco/--no-draco` | flag | `false` | Draco mesh compression |

Automatically invoked by `gameassets batch` when Part3D is enabled and `animator3d` is available.

---

### Debug Commands

#### `animator3d screenshot INPUT`

Generate multi-angle PNG screenshots of a 3D model. Designed for AI-agent debugging pipelines.

```bash
# Default 4 views, workbench renderer:
animator3d screenshot model.glb -o screenshots/

# Specific frames for animation review:
animator3d screenshot animated.glb -o frames/ --frame-list 1,24,48,72

# High-res EEVEE with bone wireframes:
animator3d screenshot rigged.glb -o debug/ --engine eevee --show-bones -r 1024
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `INPUT` | path | **required** | GLB to render |
| `-o, --output-dir` | path | `<input>_debug/` | Output directory |
| `--views` | str | `front,three_quarter,right,back` | Comma-separated view names |
| `-r, --resolution` | int | `512` | Render resolution (px) |
| `--show-bones` | flag | `false` | Show armature wireframe overlay |
| `--frame` | int | (all) | Render a single frame |
| `--frame-list` | str | (all) | Comma-separated frame numbers (e.g. `1,24,48`) |
| `--engine` | choice | `workbench` | Render engine: `workbench` or `eevee` |
| `--ortho` | flag | `false` | Orthographic camera |
| `--no-transparent-film` | flag | `false` | Opaque background (disable alpha) |

#### `animator3d inspect-rig INPUT`

Inspect rig with bone wireframe overlays and optional vertex weight heatmap.

```bash
animator3d inspect-rig rigged.glb -o rig_debug/
animator3d inspect-rig rigged.glb -o rig_debug/ --show-weights "mixamorig:Spine"
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `INPUT` | path | **required** | GLB to inspect |
| `-o, --output-dir` | path | `<input>_debug/` | Output directory |
| `--show-weights` | str | (off) | Bone name for vertex weight heatmap |
| `--views` | str | `front,three_quarter,right,back` | Comma-separated view names |
| `-r, --resolution` | int | `512` | Render resolution (px) |
| `--engine` | choice | `workbench` | Render engine: `workbench` or `eevee` |
| `--ortho` | flag | `false` | Orthographic camera |
| `--no-transparent-film` | flag | `false` | Opaque background |

---

### Common Animation Flags

All animation commands share these flags:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--draco/--no-draco` | flag | `false` | Apply Draco mesh compression on export |
| `--append/--no-append` | flag | `true` | Append clip to existing actions (`--no-append` clears all first) |
| `--clip-name` | str | auto | Override the default clip name (max 64 characters) |
| `--frames` | int | varies | Total number of animation frames |

**Append behavior:** By default (`--append`), new clips are added to any existing NLA tracks in the
GLB. Use `--no-append` to clear all existing animations before writing the new clip (useful for
single-clip exports).

---

## Quality Presets

Animator3D supports quality presets via the shared [QualityEngine](../Shared/). Pass `--quality`
to control frame counts and animation fidelity:

```bash
animator3d game-pack rigged.glb animated.glb --preset humanoid --quality high
```

| Tier | Effect |
|------|--------|
| `fast` | Fewer frames, shorter clips |
| `low` | Reduced frame counts |
| `medium` | Default frame counts |
| `high` | More frames, smoother motion |
| `highest` | Maximum frames, finest detail |

See [`docs/superpowers/specs/2026-04-30-quality-presets-design.md`](../docs/superpowers/specs/2026-04-30-quality-presets-design.md)
for full specification.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANIMATOR3D_BIN` | Override the `animator3d` binary path (used by GameAssets and other upstream tools) |

---

## Output Layout

Animator3D produces a **single animated GLB** containing all generated clips as named NLA actions.
Each clip is stored as a separate action accessible by game engines via the glTF animation array.

```text
animated_hero.glb
├── Mesh (armature-skinned)
├── Armature
│   └── Bones (with keyframe data)
└── Animations
    ├── Animator3D_BreatheIdle  (frame 1–72)
    ├── Animator3D_Walk         (frame 1–48)
    ├── Animator3D_Run          (frame 1–36)
    ├── Animator3D_Jump         (frame 1–36)
    └── Animator3D_Fall         (frame 1–24)
```

Clip names follow the `Animator3D_<Type>` convention unless overridden with `--clip-name`. The
`list-clips` command outputs the exact clip names and frame ranges:

```bash
animator3d list-clips animated.glb
```

---

## Pipeline Integration

Animator3D is the **final animation stage** in the GameDev asset pipeline:

```text
Text3D (mesh) → Paint3D (texture) → Rigging3D (armature) → Animator3D (clips) → VibeGame (browser)
```

1. **Rigging3D** produces a rigged GLB with armature and bones.
2. **Animator3D** takes the rigged GLB and generates procedural animation clips.
3. The **animated GLB** is the preferred output for VibeGame handoff via `loadGltfAnimated`
   or `loadGltfToSceneWithAnimator`.

### GameAssets batch integration

[GameAssets](../GameAssets/) calls `animator3d game-pack` automatically when animation is enabled.
The manifest `animate` column and `game.yaml` profile blocks control which preset and clips are
generated. Use `--no-animate` in GameAssets to skip this stage.

### GameDevLab debugging

Use [GameDevLab](../GameDevLab/) to verify animation quality:

```bash
gamedev-lab debug screenshot animated.glb -o review/
gamedev-lab debug inspect animated.glb
```

---

## Development

### Setup

```bash
cd Animator3D
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running tests

```bash
pytest tests
```

### Linting and formatting

```bash
ruff check .                # Lint
ruff check . --fix          # Auto-fix lint issues
ruff format .               # Format
ruff format --check .       # Check formatting without writing
```

### Key source files

| File | Description |
|------|-------------|
| `src/animator3d/cli.py` | Click CLI — all commands and flags |
| `src/animator3d/cli_rich.py` | Rich-enhanced Click setup |
| `src/animator3d/bpy_ops.py` | Blender Python operations (import, export, keyframes) |
| `src/animator3d/debug_render.py` | Screenshot and weight-heatmap rendering |
| `src/animator3d/__main__.py` | `python -m animator3d` support |

### Python module usage

Animator3D can also be used as a Python module:

```python
from animator3d import bpy_ops

bpy_ops.clear_scene()
bpy_ops.import_asset("rigged.glb")
arms = bpy_ops.list_armatures()
arm_name = arms[0].name
bpy_ops.rename_bones_from_chains(arm_name)
bpy_ops.walk_cycle_keyframes(arm_name, frame_start=1, frame_end=48,
                             cycles=2.0, leg_amp=0.14,
                             action_name="Animator3D_Walk")
bpy_ops.export_auto("animated.glb", draco=False)
```

---

## License

MIT — see [`LICENSE`](LICENSE).
