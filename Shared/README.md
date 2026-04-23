# gamedev-shared

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Shared library for the **GameDev** monorepo — common code for Text2D, Text3D, GameAssets, Texture2D, Skymap2D, Text2Sound, Rigging3D, and Materialize.

## Modules

| Module | Description |
|--------|-------------|
| `gamedev_shared.logging` | Unified Rich/ANSI logger (info, warn, error, step, header, success) |
| `gamedev_shared.cli_rich` | `rich-click`: `setup_rich_click`, `setup_rich_click_module` (returns `(click, rich_ok)`); all Python CLIs use the latter in `cli_rich.py` |
| `gamedev_shared.hf` | HF token (`get_hf_token`) and cache text for Rich (`hf_home_display_rich`) — no `huggingface_hub` dependency |
| `gamedev_shared.skill_install` | Generic Cursor Agent Skill install by `tool_name` (e.g. `rigging3d` when `SKILL.md` exists) |
| `gamedev_shared.gpu` | GPU/memory helpers (`format_bytes`, `get_gpu_info`, `clear_cuda_memory`, …) |
| `gamedev_shared.profiler` | Spans com tempo, CPU, RSS e VRAM CUDA (`ProfilerSession`, `profile_span`, `cuda_memory_snapshot_all` for all GPUs; extra `[profiler]` → `psutil`) |
| `gamedev_shared.subprocess_utils` | Subprocess execution (`resolve_binary`, `run_cmd`, `RunResult`) |
| `gamedev_shared.env` | Constants and helpers for monorepo env vars (`TOOL_BINS`, `get_tool_bin`, …) |
| `gamedev_shared.installer` | Base classes for installers (Python and Rust) |
| `gamedev_shared.installer.registry` | Registry (`ToolSpec`, `TOOLS`, `find_monorepo_root`, `try_find_monorepo_root`) |
| `gamedev_shared.installer.unified` | Unified installer — installs any tool (`gamedev-install` CLI) |
| `gamedev_shared.installer.text3d_extras` | Post-venv Text3D (nvdiffrast, `~/.config/text3d`, wrappers) |
| `gamedev_shared.installer.part3d_extras` | PyG extras (torch-scatter, torch-cluster) and Part3D summary |
| `gamedev_shared.multi_gpu` | Multi-GPU weight splitting planner (MultiGPUPlanner, DevicePlan, ModelArchitectureRegistry) — wraps accelerate for intelligent device placement |

## Usage example

```python
from gamedev_shared.logging import get_logger

log = get_logger("my_module")
log.info("Info message")
log.step("Processing item 1/10")
log.success("Done")
```

```python
from gamedev_shared.subprocess_utils import resolve_binary, run_cmd

bin_path = resolve_binary("TEXT2D_BIN", "text2d")
result = run_cmd([bin_path, "generate", "a cat"], verbose=True)
```

```python
from gamedev_shared import MultiGPUPlanner

planner = (
    MultiGPUPlanner()
    .for_model(model)
    .with_gpus([0, 1])
    .architecture("hunyuan3d")
)
plan = planner.plan()  # DevicePlan with device_map
model = planner.apply()  # Model dispatched across GPUs
```

## Unified installer

Installing `gamedev-shared` exposes the `gamedev-install` command:

```bash
gamedev-install --list                     # List tools
gamedev-install materialize                # Install Materialize (Rust)
gamedev-install text2d                    # Creates project/.venv if needed; wrappers use that Python
gamedev-install all                        # Install everything
gamedev-install materialize --action uninstall
```

You can also run without `pip install` via scripts at the monorepo root:

```bash
./install.sh materialize     # Linux/macOS
.\install.ps1 materialize    # Windows PowerShell
```

## Install

```bash
# Inside the monorepo (editable)
pip install -e Shared/

# With GPU support
pip install -e "Shared/[gpu]"

# With CLI (click + rich-click)
pip install -e "Shared/[cli]"
```

## Extras

- `gpu` — torch (for `gamedev_shared.gpu`)
- `cli` — click + rich-click (for `gamedev_shared.cli_rich`)
- `dev` — pytest

## Development

```bash
# Editable install with dev extras
pip install -e "Shared/[dev]"

# Tests
pytest Shared/tests/ -v

# Or Makefile at monorepo root
make test-shared
```

## Environment variables

Defined in `gamedev_shared.env` and used across packages:

| Variable | Description |
|----------|-------------|
| `TEXT2D_BIN` | Path to `text2d` binary (fallback: `text2d` on `PATH`) |
| `TEXT3D_BIN` | Path to `text3d` |
| `TEXT2SOUND_BIN` | Path to `text2sound` |
| `TEXTURE2D_BIN` | Path to `texture2d` |
| `SKYMAP2D_BIN` | Path to `skymap2d` |
| `RIGGING3D_BIN` | Path to `rigging3d` |
| `GAMEASSETS_BIN` | Path to `gameassets` |
| `MATERIALIZE_BIN` | Path to `materialize` |
| `HF_TOKEN` / `HUGGINGFACEHUB_API_TOKEN` | Hugging Face token (see also `gamedev_shared.hf`) |
| `HF_HOME` | Hugging Face cache directory |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator config (auto-set by the monorepo if empty) |
