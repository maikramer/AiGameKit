# Text2D — AI Text-to-Image Generation

> Fast, local text-to-image generation using [FLUX.2 Klein](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) with SDNQ quantization. Designed for modest GPUs (6 GB VRAM with `--low-vram`).

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

## Overview

Text2D is a CLI tool that generates images from text prompts using the FLUX.2 Klein model in SDNQ (4-bit dynamic quantization). It integrates with the GameDev monorepo pipeline and supports quality presets, multi-GPU inference, and batch generation.

**Default model:** [Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32](https://huggingface.co/Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32) (high VRAM) or [Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) (low VRAM).

## Requirements

| Item   | Minimum  | Notes |
|--------|----------|-------|
| Python | 3.10+    | Tested on 3.10–3.13 |
| GPU    | Optional | NVIDIA + CUDA recommended for reasonable inference |
| VRAM   | ~6 GB+   | With `--low-vram` and 512² resolution; multi-GPU via `--gpu-ids` |
| Disk   | ~8 GB    | HF cache + SDNQ weights (~2.5 GB on disk) |

> **First run** downloads several GB from Hugging Face and may take many minutes. Subsequent runs with cached weights finish in seconds to ~1 minute depending on hardware.

**Weight license:** the default SDNQ checkpoint is tied to **FLUX Non-Commercial**. For commercial use, set `TEXT2D_MODEL_ID=black-forest-labs/FLUX.2-klein-4B` (Apache 2.0, more VRAM). See [GameDev/README.md — Licenses](../README.md).

## Installation

### Monorepo (recommended)

```bash
cd /path/to/GameDev
cd Shared && pip install -e .
cd Text2D && pip install -e .
```

Or use the unified installer:

```bash
./install.sh text2d
# Equivalent: gamedev-install text2d
```

### Development setup

```bash
cd Text2D
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
text2d --help
```

With NVIDIA, `setup.sh` installs PyTorch with CUDA. For dev dependencies:

```bash
pip install -e ".[dev]"
```

Detailed guides: [docs/INSTALL.md](docs/INSTALL.md) · [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Commands

**Entry point:** `text2d` (or `python -m text2d`)

```bash
text2d --help           # List all subcommands
text2d generate --help  # Flags for generate
text2d -v generate …    # Verbose (group-level)
```

### `text2d generate PROMPT`

Generate an image from a text prompt using the FLUX pipeline.

```bash
# Basic usage — saves to outputs/images/<prompt>_<timestamp>.png
text2d generate "a cat holding a sign that says hello world"

# Custom resolution, steps, and output path
text2d generate "sunset landscape" -W 768 -H 768 -s 4 -g 1.0 -o sunset.png

# Reproducible output with seed
text2d generate "portrait" --seed 42 -o portrait.png

# Low VRAM mode (4B model instead of 9B)
text2d generate "dragon" --low-vram

# Multi-GPU: split model across GPUs 0 and 1
text2d generate "epic scene" --gpu-ids 0,1

# Quality preset (overrides resolution and steps)
text2d generate "character design" --quality high
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto | Output file path (`.png` or `.jpg`) |
| `-W, --width` | int | 1024 | Image width in pixels |
| `-H, --height` | int | 1024 | Image height in pixels |
| `-s, --steps` | int | 4 | Inference diffusion steps |
| `-g, --guidance` | float | 1.0 | Guidance scale (1.0 recommended for SDNQ) |
| `--seed` | int | — | Reproducible generation seed |
| `--cpu` | flag | off | Force CPU inference |
| `--low-vram` | flag | off | CPU offload mode (4B model, ~6 GB VRAM) |
| `-m, --model` | str | auto | Model ID override (see `text2d models`) |
| `--profile` | flag | off | Measure timing, CPU, RAM, and VRAM |
| `--gpu-ids` | str | auto | GPU IDs for multi-GPU split (e.g. `0,1`) |
| `--quality` | str | `medium` | Quality tier: `fast` / `low` / `medium` / `high` / `highest` |
| `-v, --verbose` | flag | off | Detailed log output |

When `--quality` is set and explicit `--width` / `--height` / `--steps` are **not** provided, the QualityEngine fills in the tier defaults (see [Quality Presets](#quality-presets)).

### `text2d generate-batch MANIFEST`

Batch generate multiple images from a JSON manifest file. Emits JSONL progress on stdout.

```bash
text2d generate-batch manifest.json -O outputs/ --force -v
```

Manifest format:

```json
[
  {
    "id": "hero",
    "prompt": "fantasy warrior with sword",
    "output": "hero.png",
    "width": 1024,
    "height": 1024,
    "steps": 4
  },
  {
    "id": "npc",
    "prompt": "old man in a tavern",
    "output": "npc.png"
  }
]
```

Each item requires `id`, `prompt`, and `output`. Optional per-item overrides: `width`, `height`, `steps`, `guidance_scale`, `seed`.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-O, --output-dir` | path | `.` | Base directory for output files |
| `-W, --width` | int | 1024 | Default image width |
| `-H, --height` | int | 1024 | Default image height |
| `-s, --steps` | int | 4 | Default inference steps |
| `-g, --guidance` | float | 1.0 | Default guidance scale |
| `--cpu` | flag | off | Force CPU inference |
| `--low-vram` | flag | off | CPU offload mode |
| `-m, --model` | str | auto | Model ID override |
| `--gpu-ids` | str | auto | GPU IDs for multi-GPU split |
| `--force` | flag | off | Overwrite existing files |
| `-v, --verbose` | flag | off | Detailed log output |

### `text2d info`

Display system information: Python version, PyTorch, CUDA availability, GPU details (name, VRAM), Hugging Face cache location, and default output directory.

```bash
text2d info
```

### `text2d doctor`

Run environment diagnostics: checks PyTorch installation, CUDA version, GPU VRAM usage, and Hugging Face cache path.

```bash
text2d doctor
```

### `text2d models`

List supported model IDs with notes.

```bash
text2d models
```

Output:

| ID | Notes |
|----|-------|
| `Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32` | Default (high VRAM), SDNQ 4-bit, 9B params |
| `Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic` | Default with `--low-vram`, SDNQ 4-bit, 4B params |
| `black-forest-labs/FLUX.2-klein-4B` | Alternative: full BF16, more VRAM (via `TEXT2D_MODEL_ID`) |

> GGUF weights target ComfyUI-GGUF workflows, not this CLI.

### `text2d skill install`

Install the Cursor Agent Skill (`SKILL.md`) into a game project's `.cursor/skills/text2d/` directory.

```bash
text2d skill install -t /path/to/game-project --force
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Game project root directory |
| `--force` | flag | off | Overwrite existing `SKILL.md` |

## Quality Presets

The `--quality` flag sets resolution, steps, and guidance from a unified profile system ([`QualityEngine`](../Shared/src/gamedev_shared/quality.py)). Values are **soft defaults** — explicit CLI flags always take precedence.

| Tier | Resolution | Steps | Guidance |
|------|-----------|-------|----------|
| `fast` | 512×512 | 4 | 1.0 |
| `low` | 768×768 | 4 | 1.0 |
| `medium` | 1024×1024 | 4 | 1.0 |
| `high` | 1024×1024 | 8 | 1.0 |
| `highest` | 1024×1024 | 12 | 1.5 |

```bash
text2d generate "concept art" --quality high    # 1024², 8 steps
text2d generate "thumbnail" --quality fast      # 512², 4 steps
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXT2D_BIN` | Override `text2d` binary path |
| `TEXT2D_MODEL_ID` | Alternative HF model ID (e.g. `black-forest-labs/FLUX.2-klein-4B` for Apache 2.0) |
| `HF_HOME` | Hugging Face cache directory (default: `~/.cache/huggingface`) |
| `TEXT2D_MODELS_DIR` | Local models directory (installer writes to `~/.config/text2d/config.env`) |
| `TEXT2D_OUTPUT_DIR` | Default image output directory |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA memory config (auto-set if empty) |
| `GAMEDEV_PROFILE_LOG` | Path for JSONL profiling output (used with `--profile`) |

## Output Layout

By default, images are saved to `outputs/images/`:

```
outputs/
└── images/
    ├── a_cat_holding_a_sign_1717000000.png
    ├── sunset_landscape_1717000060.png
    └── portrait_1717000120.png
```

Use `-o` to specify a custom path. Supported formats: `.png` (default) and `.jpg`/`.jpeg`.

## Pipeline Integration

Text2D is the **first step** in the GameDev batch asset pipeline:

```
Text2D (image) → Text3D (mesh) → Paint3D (textures) → Part3D (semantic parts)
```

- **GameAssets** orchestrates Text2D via subprocess, passing `--quality` from `game.yaml` generation settings.
- Text2D generates reference images that feed into **Text3D** (image → 3D generation).
- Can also produce standalone images for **Texture2D** and **Skymap2D** workflows.

## Development

```bash
cd Text2D

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .
ruff check . --fix

# Format
ruff format .
ruff format --check .
```

Test files: [`tests/test_cli.py`](tests/test_cli.py), [`tests/test_generator_unit.py`](tests/test_generator_unit.py), [`tests/test_cli_integration.py`](tests/test_cli_integration.py), [`tests/test_text2d_extended.py`](tests/test_text2d_extended.py).

## Project Layout

```
Text2D/
├── src/text2d/
│   ├── __init__.py
│   ├── __main__.py         # python -m text2d
│   ├── cli.py              # Click CLI (generate, info, models, doctor, skill)
│   ├── generator.py        # FLUX pipeline + inference (KleinFluxGenerator)
│   ├── cli_rich.py         # Rich config for CLI
│   └── utils/
│       └── memory.py       # System info, GPU detection, byte formatting
├── tests/
│   ├── test_cli.py
│   ├── test_generator_unit.py
│   ├── test_cli_integration.py
│   └── test_text2d_extended.py
├── docs/
│   ├── INSTALL.md
│   └── TROUBLESHOOTING.md
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh
│   ├── run_installer.sh
│   ├── install.sh
│   └── installer.py
├── pyproject.toml
└── README.md
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights:** default SDNQ follows [Disty0 model card](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) (non-commercial in HF metadata). BFL BF16 checkpoint: [FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (Apache 2.0). Full license table: [GameDev/README.md — Licenses](../README.md).
