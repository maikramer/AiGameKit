# gamedev-shared — Monorepo Utility Library

> Shared utility library used by **all** Python packages in the GameDev monorepo — logging, GPU management, subprocess helpers, quality presets, multi-GPU support, profiling, and installer infrastructure.

## Overview

`gamedev-shared` (`gamedev_shared`) is the foundational Python package for the GameDev monorepo. Every other Python package (Text2D, Text3D, Paint3D, GameAssets, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, Part3D, Terrain3D, GameDevLab) depends on it. **It must be installed before any other package.**

It provides reusable building blocks so each tool stays focused on its domain: structured logging, GPU detection and VRAM enforcement, subprocess execution with streaming output, a unified quality-preset engine, multi-GPU weight splitting, CPU/RAM/GPU profiling, JSONL progress reporting for batch orchestration, and a unified installer CLI.

**Version:** 0.2.0 | **License:** MIT | **Python:** >= 3.10

## Modules

| Module | Description |
|--------|-------------|
| `logging` | Shared `Logger` with Rich/ANSI structured output (`info`, `warn`, `error`, `step`, `header`, `success`) |
| `gpu` | GPU detection, VRAM monitoring, `warn_if_vram_occupied()`, `enforce_exclusive_gpu()`, `kill_gpu_compute_processes_aggressive()`, `format_bytes()`, `clear_cuda_memory()` |
| `subprocess_utils` | `run_cmd()`, `run_cmd_streaming()`, `resolve_binary()`, `merge_subprocess_output()`, `RunResult` dataclass |
| `env` | Canonical env-var constants (`TOOL_BINS`, `get_tool_bin()`, `ensure_pytorch_cuda_alloc_conf()`, `subprocess_gpu_env()`, `detect_low_vram()`) |
| `installer/` | Unified installer (`install.sh` / `python3 -m gamedev_shared.installer.unified`); registry, Python/Rust base classes, per-tool extras |
| `cli_rich` | `setup_rich_click()` / `setup_rich_click_module()` — parametrized rich-click config for all CLIs |
| `quality` | **QualityEngine** — 5 quality tiers, 14 asset categories, 11 audio kinds, soft parameter resolution with `ParameterSource` tracking |
| `multi_gpu` | **MultiGPUPlanner** — auto-detect GPUs, split weights via accelerate, `DevicePlan`, `ModelArchitectureRegistry` |
| `profiler/` | `ProfilerSession` — CPU/RAM/GPU profiling with SQLite perf DB and JSONL span output |
| `perfstore/` | SQLite perf database (`PerfDB`) for storing and querying profiling records |
| `progress` | `emit_progress()` / `emit_result()` / `parse_progress_line()` — structured JSONL progress for batch tools |
| `pipeline/` | Manifest parsing, GLB metadata extraction, validation, and caching helpers |
| `path_utils` | `safe_filename()`, `ensure_directory()` — filesystem-safe path helpers |
| `hf` | HuggingFace token resolution (`get_hf_token`) and cache display (`hf_home_display_rich`) |
| `seed_utils` | `generate_seed()`, `resolve_effective_seed()`, `seed_everything()` — reproducible generation across random/numpy/torch |
| `quantization` | `get_quantization_config()` — bitsandbytes int8/int4, torchao, quanto, FP8; `enable_vae_optimizations()`, `enable_attention_optimizations()` |
| `sdnq` | SDNQ quantization helpers — 4 tested presets (`uint8`, `int8`, `int4`, `fp8`), `quantize_model()`, `create_config()`, VRAM estimation |
| `bpy_mesh` | Mesh load/save via bpy (`load_glb()`, `save_glb()`, `load_any()`, `create_mesh_from_arrays()`, `save_colored_mesh()`) |
| `mesh_utils` | Legacy compatibility (`weld_glb()` — retained as no-op) |
| `image_utils` | `save_image_with_metadata()`, `create_thumbnail()`, `create_zip()`, `load_bytes_as_rgb()`, `ensure_rgb()` |
| `vram_monitor` | `VRAMMonitor` — live VRAM monitoring in background thread, `VRAMStats`, `find_quantization_sweet_spot()` |
| `skill_install` | `install_my_skill()` / `install_agent_skill()` — Cursor Agent Skill installation from monorepo or package source |

## Installation

```bash
# Editable install (required before any other package)
cd Shared && pip install -e .

# With GPU support (torch)
cd Shared && pip install -e ".[gpu]"

# With CLI support (rich-click)
cd Shared && pip install -e ".[cli]"

# Development dependencies (pytest, ruff, mypy)
cd Shared && pip install -e ".[dev]"

# Full dev + GPU
cd Shared && pip install -e ".[dev,gpu]"
```

### Optional Extras

| Extra | Installs | Used by |
|-------|----------|---------|
| `gpu` | `torch>=2.1.0` | `gpu`, `vram_monitor`, `multi_gpu` |
| `cli` | `rich-click>=1.8.0` | `cli_rich` |
| `quantization` | `bitsandbytes`, `torchao`, `optimum-quanto`, `sdnq` | `quantization`, `sdnq` |
| `low_vram` | `xformers` (Linux) | Low-VRAM GPU optimization |
| `profiler` | `psutil` | `profiler/` |
| `mesh` | `bpy>=5.0.1` | `bpy_mesh` |
| `dev` | `pytest`, `pytest-cov`, `ruff`, `mypy`, `Pillow` | Testing & linting |

## QualityEngine

Centralized quality-preset system used by all Python generation tools.

**5 quality tiers:** `fast` | `low` | `medium` | `high` | `highest`

- Config files: `Shared/src/gamedev_shared/data/quality-profiles.yaml` and `asset-categories.yaml`
- 14 asset categories (character, environment, prop, vehicle, texture, skymap, …)
- 11 audio kinds (footstep, impact, ambient, music, …)
- **Soft resolution:** only fills defaults when the user has not explicitly set a parameter (tracked via `ParameterSource`)
- All Python tools expose `--quality <tier>` and optionally `--category <name>`
- GameAssets uses `generation:` in `game.yaml` → maps to `--quality`

```python
from gamedev_shared.quality import QualityEngine

engine = QualityEngine()
params = engine.resolve("text2d", quality="high", category="character")
# params.width, params.height, params.steps, etc. filled from profile
```

## MultiGPUPlanner

Automatic multi-GPU weight splitting for large models.

```python
from gamedev_shared import MultiGPUPlanner

planner = (
    MultiGPUPlanner()
    .for_model(model)
    .with_gpus([0, 1])
    .architecture("hunyuan3d")
)
plan = planner.plan()   # DevicePlan with device_map
model = planner.apply() # Model dispatched across GPUs
```

- Auto-detects available GPUs via `nvidia-smi`
- Splits model weights across GPUs using `accelerate`
- Tools accept `--gpu-ids "0,1"` CLI flag
- GameAssets batch/resume propagates `--gpu-ids` and `CUDA_VISIBLE_DEVICES` to all sub-tools

## ProfilerSession

CPU/RAM/GPU profiling with SQLite storage and JSONL span output.

- **Enable:** set `GAMEDEV_PROFILE=1` or pass `--profile-tools` flag
- Records wall-clock time, CPU %, RSS memory, and CUDA VRAM per span
- Stores results in SQLite perf database (`PerfDB`)
- `gamedev-lab perf` commands for analysis and comparison

```python
from gamedev_shared.profiler import ProfilerSession

with ProfilerSession("text3d_inference") as span:
    # ... heavy GPU work ...
    pass
# span automatically records timing + memory
```

## Unified Installer

Installing `gamedev-shared` exposes the `gamedev-install` CLI:

```bash
gamedev-install --list                     # List all tools
gamedev-install materialize                # Install Materialize (Rust)
gamedev-install text2d                     # Creates .venv if needed
gamedev-install all                        # Install everything
gamedev-install materialize --action uninstall
```

Shell scripts at the monorepo root also work without pip install:

```bash
./install.sh materialize     # Linux/macOS
.\install.ps1 materialize    # Windows PowerShell
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXT2D_BIN` | Path to `text2d` binary (fallback: `text2d` on `PATH`) |
| `TEXT3D_BIN` | Path to `text3d` |
| `TEXT2SOUND_BIN` | Path to `text2sound` |
| `TEXTURE2D_BIN` | Path to `texture2d` |
| `SKYMAP2D_BIN` | Path to `skymap2d` |
| `RIGGING3D_BIN` | Path to `rigging3d` |
| `ANIMATOR3D_BIN` | Path to `animator3d` |
| `PART3D_BIN` | Path to `part3d` |
| `PAINT3D_BIN` | Path to `paint3d` |
| `TERRAIN3D_BIN` | Path to `terrain3d` |
| `GAMEASSETS_BIN` | Path to `gameassets` |
| `GAMEDEVLAB_BIN` | Path to `gamedev-lab` |
| `MATERIALIZE_BIN` | Path to `materialize` |
| `VIBEGAME_BIN` | Path to `vibegame` |
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | Hugging Face authentication token |
| `HF_HOME` | Hugging Face cache directory |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator config (auto-set if empty) |
| `GAMEDEV_PROFILE` | Set to `1` to enable profiling |
| `CUDA_VISIBLE_DEVICES` | GPU device IDs (e.g., `0,1`) |

## Development

```bash
# Editable install with dev extras
cd Shared && pip install -e ".[dev]"

# Run tests
pytest tests -v

# Or via Makefile at monorepo root
make test-shared

# Lint
ruff check .

# Format
ruff format .

# Type checking (mypy)
mypy src --ignore-missing-imports
```

## License

MIT
