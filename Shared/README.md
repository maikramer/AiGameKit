# aigamekit-shared — Monorepo Utility Library

> Shared utility library used by **all** Python packages in the AiGameKit monorepo — logging, GPU management, subprocess helpers, quality presets, multi-GPU support, profiling, and installer infrastructure.

## Overview

`aigamekit-shared` (`aigamekit_shared`) is the foundational Python package for the AiGameKit monorepo. Every other Python package (Text2D, Text3D, Paint3D, GameAssets, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, Terrain3D, AiGameKitLab) depends on it. **It must be installed before any other package.**

It provides reusable building blocks so each tool stays focused on its domain: structured logging, GPU detection and VRAM enforcement, subprocess execution with streaming output, a unified quality-preset engine, multi-GPU weight splitting, CPU/RAM/GPU profiling, JSONL progress reporting for batch orchestration, and a unified installer CLI.

**Version:** 0.2.0 | **License:** MIT | **Python:** >= 3.13, < 3.14

## Modules

| Module | Description |
|--------|-------------|
| `logging` | Shared `Logger` with Rich/ANSI console **and** daily file logs (`configure_logging`, stdlib bridge). See [docs/LOGGING.md](../docs/LOGGING.md) |
| `gpu` | GPU detection, VRAM monitoring, `warn_if_vram_occupied()`, `enforce_exclusive_gpu()`, `kill_gpu_compute_processes_aggressive()`, `format_bytes()`, `clear_cuda_memory()` |
| `subprocess_utils` | `run_cmd()`, `run_cmd_streaming()`, `resolve_binary()` (prefers `<Tool>/.venv/bin` when `AIGAMEKIT_PREFER_MONOREPO=1`), `merge_subprocess_output()`, `RunResult` |
| `cli_helpers` | UMS opts (`try_ums_delegation`, `with_ums_peak_opts`, `prepare_gpu_exclusive` — GPU prep only after UMS fail / `--no-ums`) |
| `env` | Canonical env-var constants (`TOOL_BINS`, `get_tool_bin()`, `prefer_monorepo_tools()`, `ensure_pytorch_cuda_alloc_conf()`, `subprocess_gpu_env()`) |
| `installer/` | Ponte Clified (`install.sh` → `tools.yaml`); hooks por ferramenta (`clified_hooks`, `*_extras`) |
| `cli_rich` | `setup_rich_click()` / `setup_rich_click_module(tool=…)` — rich-click config; `tool=` wires file logging |
| `quality` | **QualityEngine** — 5 quality tiers, 14 asset categories, 11 audio kinds, soft parameter resolution with `ParameterSource` tracking |
| `multi_gpu` | **MultiGPUPlanner** — auto-detect GPUs, split weights via accelerate, `DevicePlan`, `ModelArchitectureRegistry` |
| `profiler/` | `ProfilerSession` — CPU/RAM/GPU profiling with SQLite perf DB and JSONL span output |
| `perfstore/` | SQLite perf database (`PerfDB`) for storing and querying profiling records |
| `progress` | `emit_progress()` / `emit_result()` / `parse_progress_line()` — structured JSONL progress for batch tools |
| `pipeline_trace` | JSONL pipeline-stage tracing for batch orchestration (`PipelineTracer`) |
| `model_server` | **UMS client** — `delegate_to_ums()`, `submit_to_ums()`, `wait_ums_job()`, `respawn_ums_backend()`, `ensure_ums_running()`, `ensure_vram_available()`, `discover_server_pids()`; `UMS_DO_NOT_KILL_TIP`. See [ModelServer/README.md](../ModelServer/README.md) |
| `lowvram` | Model VRAM `FOOTPRINTS` registry (peak accounting for UMS admit) + `get_footprint()`, `plan_offload()` |
| `vram_budget`, `paint_budget`, `lod_budget` | Per-domain VRAM/face budgets (UMS peak signals, Paint3D views, LOD0 face targets) |
| `ums_payload`, `ums_load`, `worker_protocol`, `worker_serve`, `worker_serve_adapter_base` | UMS subprocess worker protocol + payload builders (JSONL stdin/stdout workers per tool) |
| `base_generator` | `DiffusionGeneratorBase` — shared lifecycle (warmup, unload, `_place_with_planner`, `save_image`, `generate_batch`) for all diffusion tools |
| `hardware` | `hw-auto` planning (GPU detect → model/quant/offload profile) shared across tools |
| `model_download` | `ensure_model()` — resumable HF weight download with status callback |
| `attention`, `group_offload`, `tiled_diffusion`, `diffusion_control`, `step_cache` | Diffusion inference optimizations (attention backend select, layer/group offload, tiled VAE/attention, abort control, step caching) |
| `mesh_repair`, `mesh_repair_arrays` | Mesh topology repair profiles (`topology_clean`, `pre_decimate_uv`, …); arrays backend (numpy/scipy) for UV-less meshes |
| `skin_transfer` | Bone rebind via geodesic/nearest skin transfer (`rigging3d transfer-weights`) |
| `monorepo`, `validation`, `presets`, `quaternius_fetch`, `cli_tables`, `glb_verify` | Monorepo path resolution, asset validation, preset loaders, Quaternius animation fetch, CLI table formatting, GLB verification |
| `path_utils` | `safe_filename()`, `ensure_directory()` — filesystem-safe path helpers |
| `hf` | HuggingFace token resolution (`get_hf_token`) and cache display (`hf_home_display_rich`) |
| `seed_utils` | `generate_seed()`, `resolve_effective_seed()`, `seed_everything()` — reproducible generation across random/numpy/torch |
| `quantization` | `get_quantization_config()` — bitsandbytes int8/int4, torchao, quanto, FP8; `enable_vae_optimizations()`, `enable_attention_optimizations()` |
| `sdnq` | SDNQ quantization helpers — 4 tested presets (`uint8`, `int8`, `int4`, `fp8`), `quantize_model()`, `create_config()`, VRAM estimation |
| `bpy_mesh` | Mesh load/save via bpy (`load_glb()`, `save_glb()`, `load_any()`, `create_mesh_from_arrays()`, `save_colored_mesh()`, `smooth_shade_scene()`); `import_gltf()` defaults to `bone_heuristic=TEMPERANCE` (avoids Icosphere bone-display meshes that bpy's `BLENDER` heuristic materializes) + `strip_bone_display_meshes()` (only when an armature is present) |
| `gltf_decode` | Decode glTF extensions bpy's importer rejects: `bpy_readable_glb()` context manager (KTX2/BasisU → `ktxdecompress`, meshopt on bpy<5.2 → `copy` via `@gltf-transform/cli`), `glb_extensions()` binary header parse, `bpy_decode_subcommand()`. Used by `import_gltf()` and AiGameKitLab so finished LODs (KTX2+meshopt) import without `Extension KHR_texture_basisu is not available` |
| `mesh_simplify` | Decimate COLLAPSE helpers + `pre_decimate_uv` / `post_decimate` repair profiles (Text3D LOD / simplify / to_paint) |
| `mesh_split` | Height-plane bisect for fellable trees (`SEAL_VERSION=cut-only-v1` fingerprint; GameAssets resume invalidates stale stump/top when seal drifts) |
| `image_utils` | `save_image_with_metadata()`, `create_thumbnail()`, `create_zip()`, `load_bytes_as_rgb()`, `ensure_rgb()` |
| `vram_monitor` | `VRAMMonitor` — live VRAM monitoring in background thread, `VRAMStats`, `find_quantization_sweet_spot()` |
| `skill_install` | `install_my_skill()` / `install_agent_skill()` — Cursor Agent Skill installation from monorepo or package source |

## Installation

```bash
# Editable install (required before any other package).
# Core deps (torch, rich-click, bitsandbytes/torchao/quanto/sdnq, xformers, psutil, bpy>=5.2.0)
# are pulled in automatically — there are no optional GPU/CLI/quantization extras.
cd Shared && pip install -e .

# Development dependencies (pytest, pytest-cov, ruff, mypy, clified, numpy, scipy, trimesh)
cd Shared && pip install -e ".[dev]"
```

### Optional Extras

| Extra | Installs | Used by |
|-------|----------|---------|
| `dev` | `pytest`, `pytest-cov`, `ruff`, `mypy`, `clified`, `numpy`, `scipy`, `trimesh` | Testing & linting (+ `mesh_repair*` unit deps for CI) |

> Runtime deps (`torch`, `rich-click`, `bitsandbytes`, `torchao`, `optimum-quanto`, `sdnq`, `xformers` on Linux, `psutil`, `bpy>=5.2.0`) are **core** — not behind an extra. There is **no** CLI `--low-vram` / `--memory-efficient` flag; VRAM is managed by **UMS + hw-auto** (see [ModelServer/README.md](../ModelServer/README.md)).

## QualityEngine

Centralized quality-preset system used by all Python generation tools.

**5 quality tiers:** `fast` | `low` | `medium` | `high` | `highest`

- Config files: `Shared/src/aigamekit_shared/data/quality-profiles.yaml` and `asset-categories.yaml`
- 14 asset categories (character, environment, prop, vehicle, texture, skymap, …)
- 11 audio kinds (footstep, impact, ambient, music, …)
- **Soft resolution:** only fills defaults when the user has not explicitly set a parameter (tracked via `ParameterSource`)
- All Python tools expose `--quality <tier>` and optionally `--category <name>`
- GameAssets uses `generation:` in `game.yaml` → maps to `--quality`

```python
from aigamekit_shared.quality import QualityEngine

engine = QualityEngine()
params = engine.resolve("text2d", quality="high", category="character")
# params.width, params.height, params.steps, etc. filled from profile
```

## MultiGPUPlanner

Automatic multi-GPU weight splitting for large models.

```python
from aigamekit_shared import MultiGPUPlanner

planner = (
    MultiGPUPlanner()
    .for_model(model)
    .with_gpus([0, 1])
    .architecture("hunyuan3d")
)
plan = planner.plan()   # DevicePlan with device_map
model = planner.apply() # Model dispatched across GPUs
```

- Auto-detects available GPUs via **NVML** (`detect_gpu_ids`; fallback `nvidia-smi`)
- Splits model weights across GPUs using `accelerate`
- Tools accept `--gpu-ids "0,1"` CLI flag
- GameAssets batch/resume propagates `--gpu-ids` and `CUDA_VISIBLE_DEVICES` to all sub-tools

## GPU / NVML (`aigamekit_shared.gpu`)

Primary VRAM/process queries use [`nvidia-ml-py`](https://pypi.org/project/nvidia-ml-py/) (NVML). Subprocess `nvidia-smi` remains only as fallback inside `gpu.py`.

```python
from aigamekit_shared.gpu import (
    nvml_available,
    query_gpu_free_mib,
    query_gpu_snapshot,
    list_gpu_snapshots,
    list_nvidia_compute_apps,
    detect_gpu_ids,
)

nvml_available()           # True when NVML init succeeded
query_gpu_free_mib(0)      # free MiB on device 0
list_gpu_snapshots()       # GpuSnapshot(index, name, free_mib, total_mib, used_mib, source)
list_nvidia_compute_apps() # [(pid, name, used_mib), ...]
detect_gpu_ids()           # [0, 1, ...]
```

Used by: UMS `doctor` / admit free-VRAM, GameAssets `info` / GPU preflight, Text3D `gpu-processes`, `detect_low_memory`, exclusive-GPU helpers.

**Not a substitute:** PyPI `hf-vram-calc` estimates LLM/KV-cache peaks — wrong model for diffusion UMS admit (`FOOTPRINTS` + `vram_planner`). Prefer NVML free + calibrated footprints.

## File logging

Every Python CLI (and UMS) mirrors `Logger` + stdlib logging to:

```text
~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log
```

```python
from aigamekit_shared.logging import Logger, configure_logging

configure_logging("text2d")  # called automatically via setup_rich_click_module(tool=…)
log = Logger()
log.info("generation started")
```

- Console: Rich/ANSI (unchanged)
- File: plain text, UTC timestamps
- Disable: `AIGAMEKIT_FILE_LOG=0` or `AIGAMEKIT_NO_FILE_LOG=1`
- Full guide: [docs/LOGGING.md](../docs/LOGGING.md)

## ProfilerSession

CPU/RAM/GPU profiling with SQLite storage and JSONL span output.

- **Enable:** set `AIGAMEKIT_PROFILE=1` or pass `--profile-tools` flag
- Records wall-clock time, CPU %, RSS memory, and CUDA VRAM per span
- Stores results in SQLite perf database (`PerfDB`)
- `aigamekit-lab perf` commands for analysis and comparison

```python
from aigamekit_shared.profiler import ProfilerSession

with ProfilerSession("text3d_inference") as span:
    # ... heavy GPU work ...
    pass
# span automatically records timing + memory
```

## Unified Installer

Installing `aigamekit-shared` exposes the `aigamekit-install` CLI:

```bash
aigamekit-install --list                     # List all tools
aigamekit-install materialize                # Install Materialize (Rust)
aigamekit-install text2d                     # Creates .venv if needed
aigamekit-install all                        # Install everything
aigamekit-install materialize --action uninstall
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
| `PAINT3D_BIN` | Path to `paint3d` |
| `TERRAIN3D_BIN` | Path to `terrain3d` |
| `GAMEASSETS_BIN` | Path to `gameassets` |
| `AIGAMEKITLAB_BIN` | Path to `aigamekit-lab` |
| `MATERIALIZE_BIN` | Path to `materialize` |
| `VIBEGAME_BIN` | Path to `vibegame` |
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | Hugging Face authentication token |
| `HF_HOME` | Hugging Face cache directory |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator config (auto-set if empty) |
| `AIGAMEKIT_PROFILE` | Set to `1` to enable profiling |
| `CUDA_VISIBLE_DEVICES` | GPU device IDs (e.g., `0,1`) |
| `AIGAMEKIT_LOG_DIR` | Daily log directory (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | Exact log file path |
| `AIGAMEKIT_LOG_TOOL` | Tool name segment in log filename |
| `AIGAMEKIT_LOG_LEVEL` | Min file level (`DEBUG`/`INFO`/`WARN`/`ERROR`) |
| `AIGAMEKIT_FILE_LOG` | `0` off; `1` force on (needed under pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | `1` disables file logging |

## Development

```bash
# Editable install with dev extras
cd Shared && pip install -e ".[dev]"

# Run tests
pytest tests -v
# Coverage floor (QualityEngine, UMS helpers, path/seed/hardware, …):
pytest tests/test_shared_coverage_100.py -q

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
