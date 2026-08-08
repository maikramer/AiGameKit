# Text2D — AI Text-to-Image Generation

> Fast, local text-to-image generation using [FLUX.2 Klein](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) with SDNQ quantization. Designed for modest GPUs (6 GB VRAM; hw-auto engata o modo memory-efficient automaticamente).

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

## Overview

Text2D is a CLI tool that generates images from text prompts using the FLUX.2 Klein model in SDNQ (4-bit dynamic quantization). It integrates with the AiGameKit monorepo pipeline and supports quality presets, multi-GPU inference, and batch generation.

**Default model:** [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (low VRAM, public) or [FLUX.2 Klein 9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) (high VRAM, **gated** — accept terms on the Hub). Both load the official BFL fp16 base; SDNQ quantization is applied at **runtime** (no pre-quantized checkpoint by default). Pre-quantized [Disty0 mirrors](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) are optional via `TEXT2D_MODEL_ID`.

## Requirements

| Item   | Minimum  | Notes |
|--------|----------|-------|
| Python | 3.13+    | Pinned `>=3.13,<3.14` (`pyproject.toml`) |
| GPU    | Optional | NVIDIA + CUDA recommended for reasonable inference |
| VRAM   | ~6 GB+   | With hw-auto memory-efficient mode (on by default) and 512² resolution; multi-GPU via `--gpu-ids` |
| Disk   | ~8 GB    | HF cache + SDNQ weights (~2.5 GB on disk) |

> **First run** downloads several GB from Hugging Face and may take many minutes. Subsequent runs with cached weights finish in seconds to ~1 minute depending on hardware.

**Weight license:** the default 4B base is **Apache 2.0** (public Hub download); the 9B base is **gated** (accept BFL terms + `HF_TOKEN`). SDNQ runtime quantization itself is MIT ([Disty0/sdnq](https://github.com/Disty0/sdnq)). See [AiGameKit/README.md — Licenses](../README.md).

## Installation

### Monorepo (recommended)

```bash
cd /path/to/AiGameKit
cd Shared && pip install -e .
cd Text2D && pip install -e .
```

Or use the unified installer:

```bash
./install.sh text2d
# Equivalent: aigamekit-install text2d
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

# Low VRAM (4B model auto-selected by hw-auto)
text2d generate "dragon"

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
| `-m, --model` | str | auto | Model ID override (see `text2d models`) |
| `--profile` | flag | off | Measure timing, CPU, RAM, and VRAM |
| `--gpu-ids` | str | auto | GPU IDs for multi-GPU split (e.g. `0,1`) |
| `--quality` | str | `medium` | Quality tier: `fast` / `low` / `medium` / `high` / `highest` |
| `--hw-auto/--no-hw-auto` | flag | on | Hardware auto-detection: enables CPU offload + 4B model on small GPUs (<7.5 GB), keeps 9B / full-GPU / multi-GPU split on big rigs. Explicit flags win. Env kill-switch: `TEXT2D_HW_AUTO=0` |
| `--compile/--no-compile` | flag | off (`generate`); **on** (`generate-batch`) | `torch.compile` on the transformer (~−6–10% hot; cold warmup costly) |
| `--compile-mode` | str | `default` | `default` / `reduce-overhead` / `max-autotune` (Inductor; reduce-overhead only with full-GPU) |
| `--channels-last/--no-channels-last` | flag | off (`generate`); **on** (`generate-batch`) | NHWC memory format (Ampere+); pairs well with compile |
| `-v, --verbose` | flag | off | Detailed log output |

When `--quality` is set and explicit `--width` / `--height` / `--steps` are **not** provided, the QualityEngine fills in the tier defaults (see [Quality Presets](#quality-presets)).

Kernel opts on ~6 GB: prefer compile+channels-last for **batch/vramd** (defaults on); keep one-shot `generate` opt-in. Details: [`docs/findings/KERNEL_OPTS_FINDINGS.md`](../docs/findings/KERNEL_OPTS_FINDINGS.md).

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
| `-m, --model` | str | auto | Model ID override |
| `--gpu-ids` | str | auto | GPU IDs for multi-GPU split |
| `--hw-auto/--no-hw-auto` | flag | on | Hardware auto-detection (offload/model/multi-GPU); `TEXT2D_HW_AUTO=0` disables |
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
| `black-forest-labs/FLUX.2-klein-9B` | Default (high VRAM), fp16 base + SDNQ runtime quantization (**gated** — accept terms on Hub) |
| `black-forest-labs/FLUX.2-klein-4B` | Auto-selected by hw-auto on small GPUs (<7.5 GB), fp16 base + SDNQ runtime (public) |
| `Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic` | Optional pre-quantized checkpoint via `TEXT2D_MODEL_ID` (declares `flux-non-commercial-license`) |

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

## Unified Model Server (vramd)

`text2d generate` auto-delegates to **`vramd`** — the monorepo GPU supervisor (one process, one socket, job queue with priority + VRAM affinity, weight+LRU eviction, subprocess workers per tool). Auto-starts on first generate unless `VRAMD_AUTO_START=0`.

```bash
text2d generate "cat" -o cat.png --vramd-stream          # queue/progress events
text2d generate "cat" -o cat.png --vramd-priority batch
text2d generate "cat" -o cat.png --no-vramd              # force in-process
vramd status                                             # backends + HOLDING/QUEUE
vramd respawn text2d                                     # reload edited src/ code
```

After editing `*/src/`: `vramd respawn text2d`. Full guide: [`Vramd/README.md`](../Vramd/README.md).

## Quality Presets

The `--quality` flag sets resolution, steps, and guidance from a unified profile system ([`QualityEngine`](../Shared/src/aigamekit_shared/quality.py)). Values are **soft defaults** — explicit CLI flags always take precedence.

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
| `AIGAMEKIT_PROFILE_LOG` | Path for JSONL profiling output (used with `--profile`) |

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

Text2D is the **first step** in the AiGameKit batch asset pipeline:

```
Text2D (image) → Text3D (mesh) → Paint3D (textures)
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
- **Weights:** default = official BFL fp16 base ([FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) Apache 2.0, public; [9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) **gated**) with SDNQ runtime quantization (MIT, [Disty0/sdnq](https://github.com/Disty0/sdnq)). Optional pre-quantized [Disty0 mirrors](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) declare `flux-non-commercial-license`. Full license table: [AiGameKit/README.md — Licenses](../README.md).
