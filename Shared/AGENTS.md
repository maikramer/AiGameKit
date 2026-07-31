# AGENTS.md — aigamekit-shared

## OVERVIEW

Foundation library for ALL Python packages in the monorepo. 47 files, 7122 LOC + 666 lines YAML data. Install first: `cd Shared && pip install -e .`. Strictly typed (`disallow_untyped_defs = True` in mypy.ini).

## WHERE TO LOOK (by downstream import frequency)

| Module | Lines | Imported By | Role |
|--------|-------|-------------|------|
| `bpy_mesh.py` | ~300 | 36 files (Text3D, Rigging3D, Animator3D, …) | `load_glb`, `save_glb`, `smooth_shade_scene` (**weld 1e-4 + smooth** — anti V/Tri≈3); `import_gltf()` (`TEMPERANCE` + decode KTX2/meshopt); `strip_bone_display_meshes()` (só com armature) |
| `gltf_decode.py` | ~200 | bpy_mesh, AiGameKitLab, Animator3D | `bpy_readable_glb()` — KTX2/`ktxdecompress`, meshopt-on-old-bpy via `@gltf-transform/cli` |
| `mesh_simplify.py` | ~240 | Text3D LOD / simplify / to_paint | Decimate COLLAPSE + `pre_decimate_uv` / `post_decimate` |
| `mesh_split.py` | ~400 | GameAssets tree split, Text3D `split-at-height` | Bisect cut-only; `SEAL_VERSION=cut-only-v1` for resume invalidation |
| `gpu.py` | ~900 | 31 files | GPU detection, VRAM (NVML→nvidia-smi), exclusive GPU, process mgmt |
| `quality.py` | 233 | 22 files | QualityEngine: 5 tiers, 14 categories, soft parameter resolution |
| `quantization.py` | 438 | 22 files | Multi-backend quant: bitsandbytes, torchao, quanto, FP8 |
| `sdnq.py` | 407 | 16 files | SDNQ quantization: 4 presets, LoRA patch, VRAM estimation |
| `profiler/*` | ~650 | 19 files | ProfilerSession, PerfRecorder, CUDA snapshots, SQLite perf DB |
| `env.py` | 133 | 10+ files | `TOOL_BINS`, `prefer_monorepo_tools` (`AIGAMEKIT_PREFER_MONOREPO`), `subprocess_gpu_env` |
| `subprocess_utils.py` | 169 | GameAssets | `resolve_binary` → `<Tool>/.venv/bin` first; `run_cmd` / streaming |
| `cli_helpers.py` | ~600 | All GPU CLIs | `try_ums_delegation`, `with_ums_peak_opts`; `prepare_gpu_exclusive` only after UMS fail / `--no-ums` |
| `progress.py` | 175 | 9 files | JSONL progress protocol: `emit_progress`, `emit_result` |
| `multi_gpu.py` | 288 | GPU tools | MultiGPUPlanner fluent builder, accelerate dispatch |
| `cli_rich.py` | ~90 | All CLIs | `setup_rich_click`, `setup_rich_click_module(tool=…)` — `tool=` → file logging |
| `logging.py` | ~450 | All Python tools + UMS | Logger Rich/ANSI + daily file sink + stdlib bridge |
| `installer/*` | 1372 | install.sh, aigamekit-install | BaseInstaller, Clified bridge, per-package hooks |

## SUBSYSTEMS

1. **GPU/VRAM** (`gpu.py` + `vram_monitor.py` + `cli_helpers.py`): NVML-first inventory; UMS delegation before prep; `prepare_gpu_exclusive` only in-process fallback; kill respects UMS queue; legacy servers need `AIGAMEKIT_ALLOW_LEGACY_SERVER=1`.
2. **Quality** (`quality.py` + `data/*.yaml`): QualityEngine resolves `--quality` + `--category` to concrete params. Soft resolution: fills only `None` fields (tracked via `ParameterSource` enum).
3. **Quantization** (`quantization.py` + `sdnq.py`): Multi-backend (bitsandbytes, torchao, quanto, FP8). SDNQ: 4 presets (`int4_dynamic`, `int4_static`, `int8`, `fp8`). VRAM estimation pre-flight.
4. **Installer** (`installer/` 10 files): `BaseInstaller` base class, `clified_hooks` per-package post-install, `unified.py` Clified bridge. Cross-deps: text3d needs nvdiffrast, rigging3d needs inference env.
5. **Profiler** (`profiler/` + `perfstore/`): `AIGAMEKIT_PROFILE=1` enables. Session spans, PerfRecorder, SQLite perf.db, CUDA memory snapshots, report formatting.
6. **File logging** (`logging.py`): process-wide sink → `~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log`. `configure_logging(tool)` + `Logger`; stdlib root bridged once. Off under pytest unless `AIGAMEKIT_FILE_LOG=1`. Docs: `docs/LOGGING.md`.
7. **Pipeline** (`pipeline/` 5 files): Manifest parsing, GLB binary metadata extraction, validation rules, caching. **Unused. Candidate for removal.**
8. **Core utilities**: env vars (`TOOL_BINS` dict), subprocess runner, JSONL progress protocol, image utils, seed utils, bpy mesh I/O.

## KEY PATTERNS

- **Lazy imports**: torch, accelerate, sdnq, bpy imported inside functions to avoid `ImportError` when deps not installed.
- **Soft parameter resolution**: QualityEngine only fills params the user hasn't explicitly set (tracked via `ParameterSource` enum).
- **Fluent builder**: `MultiGPUPlanner.for_model().with_gpus().architecture().plan().apply()`.
- **JSONL progress protocol**: `emit_progress(stream=sys.stderr, stage="...", progress=0.5, message="...")`. Orchestrator parses via `parse_progress_line`.
- **Protected process list**: `kill_gpu_compute_processes_aggressive` never kills X11/compositor/system processes.
- **YAML-driven config**: `quality-profiles.yaml` (5 tiers x 10 tools) and `asset-categories.yaml` (14 categories + 17 audio kinds).
- **File logging via CLI bootstrap**: every package `cli_rich.py` passes `tool=` to `setup_rich_click_module` → `configure_logging`. UMS calls `configure_logging("ums")` in `start`. Prefer `Logger.info(..., console=False)` for file-only lines.

## ANTI-PATTERNS

- `pipeline/` module is unused. Don't add new code there.
- `mesh_utils.py` is a legacy no-op (`weld_glb` stub). Use Text3D's `export.py` instead.
- `data/asset-categories.yaml` has 17 audio kinds (root README says 11, which is outdated).
- `__init__` exports only 3 symbols (`MultiGPUPlanner`, `DevicePlan`, `ModelArchitectureRegistry`). Everything else via direct submodule import.
- GPU kill functions have a protected process list. Never remove entries from it.
- Do not document public `--low-vram` / `--memory-efficient` CLI — peak signals are payload-only (hw-auto / `with_ums_peak_opts`).

## DATA FILES

- `data/quality-profiles.yaml` (283 lines): 5 tiers (`fast`/`low`/`medium`/`high`/`highest`) x 10 tools with concrete parameter values.
- `data/asset-categories.yaml` (383 lines): 14 asset categories + 17 audio kinds with target face counts and hints.

## TESTS

25+ test files. Key: `test_logging.py`, `test_gpu.py` / `test_gpu_ums_kill.py`,
`test_quality.py`, `test_sdnq.py` (`apply_quantized_matmul`), `test_path_utils.py`
(`safe_filename`), `test_model_server*.py`, `test_env.py`, `test_mesh_repair.py`,
`test_shared_coverage_100.py` (CPU floor: QualityEngine, UMS helpers, hardware).

Run: `make test-shared` **só** se `Shared/.venv` tiver pytest+torch (extra
`[gpu]`). Sem venv local, `make` cai no `python3` do PATH → fails falsos
(`No module named torch`). Alternativa:

```bash
cd Shared && ../GameAssets/.venv/bin/python -m pytest -q
# ou: pip install -e ".[dev,gpu]" num venv 3.13
```

Kill GPU tests: `patch(aigamekit_shared.gpu.os.kill)` = patch **global** `os.kill`
— isolar UMS com mocks `is_ums_running` / `discover_server_pids` (ver
`docs/findings/UMS_VRAM_FINDINGS.md` § Testes).

Monorepo guide: [`docs/TESTING.md`](../docs/TESTING.md) · [`docs/TESTING_PT.md`](../docs/TESTING_PT.md).
