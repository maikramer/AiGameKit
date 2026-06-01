# AGENTS.md — gamedev-shared

## OVERVIEW

Foundation library for ALL Python packages in the monorepo. 47 files, 7122 LOC + 666 lines YAML data. Install first: `cd Shared && pip install -e .`. Strictly typed (`disallow_untyped_defs = True` in mypy.ini).

## WHERE TO LOOK (by downstream import frequency)

| Module | Lines | Imported By | Role |
|--------|-------|-------------|------|
| `bpy_mesh.py` | 270 | 36 files (Text3D, Part3D, Rigging3D, GameAssets, Animator3D) | `load_glb`, `save_glb`, `load_any`, `create_mesh_from_arrays` |
| `gpu.py` | 506 | 31 files | GPU detection, VRAM monitoring, exclusive GPU, nvidia-smi process mgmt |
| `quality.py` | 233 | 22 files | QualityEngine: 5 tiers, 14 categories, soft parameter resolution |
| `quantization.py` | 438 | 22 files | Multi-backend quant: bitsandbytes, torchao, quanto, FP8 |
| `sdnq.py` | 407 | 16 files | SDNQ quantization: 4 presets, LoRA patch, VRAM estimation |
| `profiler/*` | ~650 | 19 files | ProfilerSession, PerfRecorder, CUDA snapshots, SQLite perf DB |
| `env.py` | 133 | 10+ files | `TOOL_BINS` dict, `ensure_pytorch_cuda_alloc_conf`, `subprocess_gpu_env` |
| `subprocess_utils.py` | 169 | GameAssets | `run_cmd`, `run_cmd_streaming`, `resolve_binary`, `RunResult` |
| `progress.py` | 175 | 9 files | JSONL progress protocol: `emit_progress`, `emit_result` |
| `multi_gpu.py` | 288 | GPU tools | MultiGPUPlanner fluent builder, accelerate dispatch |
| `cli_rich.py` | 78 | All CLIs | `setup_rich_click`, `setup_rich_click_module` |
| `logging.py` | 122 | 10 files | Logger with Rich/ANSI fallback |
| `installer/*` | 1372 | install.sh, gamedev-install | BaseInstaller, Clified bridge, per-package hooks |

## SUBSYSTEMS

1. **GPU/VRAM** (`gpu.py` + `vram_monitor.py`): detect gpus, enforce exclusive GPU, kill competing processes (with protected process list), live VRAM monitoring thread.
2. **Quality** (`quality.py` + `data/*.yaml`): QualityEngine resolves `--quality` + `--category` to concrete params. Soft resolution: fills only `None` fields (tracked via `ParameterSource` enum).
3. **Quantization** (`quantization.py` + `sdnq.py`): Multi-backend (bitsandbytes, torchao, quanto, FP8). SDNQ: 4 presets (`int4_dynamic`, `int4_static`, `int8`, `fp8`). VRAM estimation pre-flight.
4. **Installer** (`installer/` 10 files): `BaseInstaller` base class, `clified_hooks` per-package post-install, `unified.py` Clified bridge. Cross-deps: text3d needs nvdiffrast, rigging3d needs inference env, part3d needs torch-scatter.
5. **Profiler** (`profiler/` + `perfstore/`): `GAMEDEV_PROFILE=1` enables. Session spans, PerfRecorder, SQLite perf.db, CUDA memory snapshots, report formatting.
6. **Pipeline** (`pipeline/` 5 files): Manifest parsing, GLB binary metadata extraction, validation rules, caching. **Unused. Candidate for removal.**
7. **Core utilities**: env vars (`TOOL_BINS` dict), subprocess runner, JSONL progress protocol, logging, image utils, seed utils, bpy mesh I/O.

## KEY PATTERNS

- **Lazy imports**: torch, accelerate, sdnq, bpy imported inside functions to avoid `ImportError` when deps not installed.
- **Soft parameter resolution**: QualityEngine only fills params the user hasn't explicitly set (tracked via `ParameterSource` enum).
- **Fluent builder**: `MultiGPUPlanner.for_model().with_gpus().architecture().plan().apply()`.
- **JSONL progress protocol**: `emit_progress(stream=sys.stderr, stage="...", progress=0.5, message="...")`. Orchestrator parses via `parse_progress_line`.
- **Protected process list**: `kill_gpu_compute_processes_aggressive` never kills X11/compositor/system processes.
- **YAML-driven config**: `quality-profiles.yaml` (5 tiers x 10 tools) and `asset-categories.yaml` (14 categories + 17 audio kinds).

## ANTI-PATTERNS

- `pipeline/` module is unused. Don't add new code there.
- `mesh_utils.py` is a legacy no-op (`weld_glb` stub). Use Text3D's `export.py` instead.
- `data/asset-categories.yaml` has 17 audio kinds (root README says 11, which is outdated).
- `__init__` exports only 3 symbols (`MultiGPUPlanner`, `DevicePlan`, `ModelArchitectureRegistry`). Everything else via direct submodule import.
- GPU kill functions have a protected process list. Never remove entries from it.

## DATA FILES

- `data/quality-profiles.yaml` (283 lines): 5 tiers (`fast`/`low`/`medium`/`high`/`highest`) x 10 tools with concrete parameter values.
- `data/asset-categories.yaml` (383 lines): 14 asset categories + 17 audio kinds with target face counts and hints.

## TESTS

25 test files, 3371 LOC. Test-to-source ratio: 0.47:1. Key files: `test_gpu.py`, `test_quality.py`, `test_sdnq.py`, `test_subprocess_utils.py`, `test_env.py`, `test_bpy_mesh.py`. Run: `make test-shared` or `pytest tests/ -v`.
