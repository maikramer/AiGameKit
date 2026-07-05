# Rigging3D — Auto-Rigging for 3D Models

> Automated rigging (skeleton + skinning in a single autoregressive pass) for 3D meshes, powered by [SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) / TokenRig (MIT), the successor to UniRig. Turns a static GLB/OBJ into a fully rigged model ready for animation.

**Version:** 0.6.0 · **Language:** Python 3.13 · **License:** MIT

---

## Overview

Rigging3D is a CLI tool that generates a rigged GLB from a static mesh in a single
model call: SkinTokens' TokenRig is a unified autoregressive model that predicts
skeleton *and* per-vertex skinning weights together (unlike the predecessor UniRig,
which used two separate models — skeleton AR + a sparse-conv skin model). The
`pipeline` command runs the whole thing end-to-end.

Typical use-case: take a static 3D character from Text3D/GameAssets and produce a
rigged GLB that Animator3D can animate with clip commands (`run`, `jump`, `fall`).

---

## Installation

### Prerequisites

- **Python 3.13** — `bpy==5.1.x` (PyPI) only ships `cp313` wheels.
- **NVIDIA GPU with CUDA** — no hard minimum documented by upstream is required in
  practice; measured peak ~3.9GB VRAM on a dense, multi-part real asset on an
  RTX 4050 (6GB), with default settings (`--num-beams 10`, no quantization). See
  `docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md` in the monorepo root for the full
  measurement writeup.
- **Model checkpoints** — downloaded automatically from
  [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) on first run
  (~1.6GB), cached under `~/.cache/rigging3d/skintokens/` (override with
  `RIGGING3D_SKINTOKENS_HOME`).

> **No `flash-attn` required.** The pipeline runs on PyTorch's native SDPA
> (`torch.nn.functional.scaled_dot_product_attention`) throughout — no native
> extension to build.

### Official installer (monorepo)

```bash
cd /path/to/GameDev
./install.sh rigging3d
```

### Manual install

```bash
# Shared first (required dependency)
cd Shared && pip install -e .

cd Rigging3D && python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Global Flags

These flags apply to all `rigging3d` subcommands.

```bash
rigging3d [GLOBAL_FLAGS] <COMMAND> [COMMAND_FLAGS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--profiler` | flag | `false` | Enable performance profiling (writes to perf DB). |
| `--gpu-ids` | str | None | GPU ID for the process (e.g., `"0"`). Propagates `CUDA_VISIBLE_DEVICES`. |
| `--hw-auto/--no-hw-auto` | flag | `true` | Hardware auto-detection: on multi-GPU rigs pins the model to the GPU with the most free VRAM; warns on very small cards (<4GB). Explicit `--gpu-ids` wins. Env kill-switch: `RIGGING3D_HW_AUTO=0` |
| `--version` | — | — | Show version and exit. |

---

## Commands

### `rigging3d pipeline`

Generates a rigged GLB — skeleton + skin in a single autoregressive pass.

```bash
rigging3d pipeline -i character.glb -o character_rigged.glb

# Reproducible seed and quality preset
rigging3d pipeline -i character.glb -o character_rigged.glb --seed 42 --quality high

# Already has a skeleton (e.g. from a previous run) — generate skin only
rigging3d pipeline -i character_with_skeleton.glb -o character_rigged.glb --use-existing-skeleton

# Raw export (no reattachment to original mesh/texture/scale)
rigging3d pipeline -i character.glb -o character_rigged.glb --no-transfer

# Tune generation quality/speed trade-off directly
rigging3d pipeline -i character.glb -o character_rigged.glb --num-beams 4 --temperature 0.8
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-i, --input` | path | **required** | Input mesh (GLB/OBJ). |
| `-o, --output` | path | **required** | Output rigged GLB. |
| `--seed` | int | `123` | Reproducible seed. |
| `--use-existing-skeleton` | flag | `false` | `--input` already has a skeleton; only skin is generated. |
| `--transfer/--no-transfer` | flag | `true` | Reattach the rig to the original mesh/texture/scale (equivalent to the old `merge` step). |
| `--postprocess` | flag | `false` | Voxel-based skin smoothing. Requires `pip install open3d` (not a package dependency). |
| `--groups-per-vertex` | int | `4` | Maximum bone influences per vertex. |
| `--num-beams` | int | `10` | Beam search width — higher = more quality/time. |
| `--top-k` | int | `5` | Top-k sampling. |
| `--top-p` | float | `0.95` | Top-p (nucleus) sampling. |
| `--temperature` | float | `1.0` | Sampling temperature. |
| `--repetition-penalty` | float | `2.0` | Repetition penalty. |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` (resolves `--groups-per-vertex` when not explicitly set). |

**Bone naming**: TokenRig predicts generic `bone_N` names for classes not covered by
its bundled `configs/skeleton/{mixamo,vroid}.yaml` maps (which is the case for the
`articulation_xl` checkpoint used here). After generation, `pipeline` runs a
topology-based classifier (`_rename_generic_bones`) that assigns Mixamo-style names
(Hips, Spine, LeftArm, RightUpLeg, …) by structural role — parent/child tree shape,
not position — so it's robust to any bone transform.

**Origin validation**: after generation, the pipeline warns if the model's base is
far from Y≈0, indicating the mesh origin may not be at the feet. Regenerate with
`text3d reorigin-feet` to fix (can't be corrected here without dropping the armature).

---

### `rigging3d transfer-weights`

Stage 8 of the master pipeline — transfers skin weights from a rigged high-poly GLB
to LOD0/1/2 targets. Independent of the generation backend (works the same
regardless of how the source GLB was rigged).

```bash
rigging3d transfer-weights -s character_rigged_hi.glb -t character_lod0.glb -t character_lod1.glb
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-s, --source` | path | **required** | Rigged high-poly GLB (output of `rigging3d pipeline`). |
| `-t, --target` | path | **required**, multiple | Target GLB(s) — use multiple times for LOD0/1/2. |
| `-o, --output` | path | None, multiple | Explicit output paths (1:1 with `--target`). Defaults to `<target>_rigged.glb`. |
| `--output-dir` | path | None | Common output folder. |
| `--output-suffix` | str | `_rigged` | Suffix applied when `--output` isn't given. |
| `--finish/--no-finish` | flag | `true` | Apply `gltf_transform_finish` (dedup+prune+KTX2+meshopt+tangents) to outputs. |

---

## Quality Presets

Rigging3D integrates with the monorepo's [QualityEngine](../Shared/src/gamedev_shared/quality/)
for soft parameter resolution. The `--quality` flag on `pipeline` fills
`--groups-per-vertex` when the user hasn't explicitly set it.

```bash
rigging3d pipeline -i mesh.glb -o rigged.glb --quality high
```

User-specified flags always take precedence over quality preset defaults.

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `RIGGING3D_SKINTOKENS_HOME` | Checkpoint cache directory (default `~/.cache/rigging3d/skintokens/`). |
| `RIGGING3D_HW_AUTO` | Set to `0` to disable hardware auto-detection (fallback if `--no-hw-auto` isn't set). |
| `CUDA_VISIBLE_DEVICES` | GPU visibility (propagated automatically when `--gpu-ids` is set). |

---

## Pipeline Integration

Rigging3D fits into the monorepo asset pipeline as follows:

```
Text3D / Paint3D  →  Part3D (optional)  →  Rigging3D  →  Animator3D
     │                     │                    │              │
  static GLB          _parts.glb          _rigged.glb     animated GLB
```

- **Input preference:** When a `_parts.glb` exists (from Part3D decomposition), the pipeline uses it as input; otherwise falls back to the base mesh.
- **GameAssets batch:** `gameassets batch` orchestrates the full flow automatically, propagating `--gpu-ids` and `CUDA_VISIBLE_DEVICES` to Rigging3D.
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

The vendored SkinTokens code in `src/rigging3d/skintokens/` is excluded from linting (ruff).

---

## Migration notes (UniRig → SkinTokens)

Rigging3D used to wrap [UniRig](https://github.com/VAST-AI-Research/UniRig)
(two-stage: skeleton AR model + sparse-conv skin model). It now wraps
[SkinTokens](https://github.com/VAST-AI-Research/SkinTokens)/TokenRig (unified
single-stage autoregressive model), vendored under `src/rigging3d/skintokens/`.
The old `skeleton`/`skin`/`merge` subcommands and `--root`/`--python` flags were
removed in this cut — there's no clean 1:1 equivalent once generation collapses to
one model call; `pipeline` covers all of it (`--use-existing-skeleton` replaces
`skin`, `--no-transfer` replaces the raw `merge`-less export path). See
`docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md` in the monorepo root for the full
migration plan, risk log, and VRAM measurements.

---

## License

- **Rigging3D CLI:** MIT — [`LICENSE`](LICENSE)
- **SkinTokens code:** MIT — [`skintokens/LICENSE`](src/rigging3d/skintokens/LICENSE)
- **HuggingFace weights:** the [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) repository card contains licensing terms — review before use.
