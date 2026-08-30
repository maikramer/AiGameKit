# AGENTS.md — AiGameKit Monorepo

Guide for agentic coding agents working in this repository.

## Mission

**Make game-asset generation effortless.** A person (or an AI) states intent; the stack produces playable game content. Pipeline complexity, model weights, and GPU memory stay inside the tools — not on the operator's plate.

Deep dives (one doc per block): [`docs/mission/`](docs/mission/README.md).

### North star

Anyone who opens this repository — human or cold AI agent — can get the **same class of results on the first serious attempt**. Following this file plus the CLIs is enough. Tribal knowledge, secret flags, and "works on my machine" GPU rituals are regressions.

→ [`docs/mission/01-north-star.md`](docs/mission/01-north-star.md)

### Premises

1. **Ease over knobs.** Prefer one command that finishes (`gameassets batch`, `gameassets dream`, tool `generate`) over exposing every model flag. Quality presets and soft defaults beat expert tuning as the primary path.
   → [`docs/mission/02-ease-over-knobs.md`](docs/mission/02-ease-over-knobs.md)

2. **Automate to the edge.** Stages chain without babysitting: shape → clean → paint → bake → LOD → rig → animate → validate → handoff. Resume, profile autodetection, and orchestration exist so neither humans nor agents re-learn the DAG each run.
   → [`docs/mission/03-automate-to-the-edge.md`](docs/mission/03-automate-to-the-edge.md)

3. **Agent-first reproducibility.** Docs, CLIs, and env contracts must be sufficient for a fresh agent to succeed. Same inputs + same quality tier ⇒ same deliverable class. Ambiguity in the happy path is a bug in the product, not a training gap for the user.
   → [`docs/mission/04-agent-first-reproducibility.md`](docs/mission/04-agent-first-reproducibility.md)

4. **VRAM is infrastructure, not a user problem.** Nobody should plan peak memory, juggle which models fit, or keep GPU occupancy in their head — regardless of how large the models are or how many backends a tool owns. The Unified Model Server (vramd) owns admit, queue, eviction, and peak accounting so that:
   - the GPU stays **busy** when there is work (high utilization);
   - VRAM stays **inside a safe margin** at all times;
   - model count and model size change **latency and queue order**, not the mental model (no new manual VRAM checklist).
   → [`docs/mission/05-vram-as-infrastructure.md`](docs/mission/05-vram-as-infrastructure.md)

### Implications for agents changing this repo

- Optimize for "first command works" and "batch finishes alone."
- Route GPU work through vramd; never teach kill/race/pkill as the normal path.
- Hide VRAM math behind admit / peak / quant / soft-fill; surface progress and artifacts, not memory spreadsheets.
- When adding models or stages, extend the coordinator and defaults — do not add operator burden.
- Document the happy path so the next AI gets identical results without this conversation.

→ [`docs/mission/06-implications-for-agents.md`](docs/mission/06-implications-for-agents.md)

## Agent reference index (optional deep dives)

Este ficheiro é o **contrato curto**: missão, mapa do monorepo, comandos, regras
duras (git=`main`, vramd, LOD0). Tribal knowledge longa vive nos docs abaixo —
**abrir só quando a tarefa tocar nesse domínio**.

| Tarefa | Doc |
|--------|-----|
| Escrever / regenerar assets (`manifest.yaml`, Omni, size_m, octree) | [`docs/MANIFEST_AUTHORING.md`](docs/MANIFEST_AUTHORING.md) |
| Relação empírica octree × faces (`_shape`) | [`docs/findings/OCTREE_FACES_FINDINGS.md`](docs/findings/OCTREE_FACES_FINDINGS.md) |
| Omni bbox/pose/clip/fingerprint | [`docs/OMNI_SHAPE_FINDINGS.md`](docs/OMNI_SHAPE_FINDINGS.md) |
| Batch vramd / waves | [`docs/GAMEASSETS_UMS_BATCH.md`](docs/GAMEASSETS_UMS_BATCH.md) |
| DAG mesh Round 3 / LOD0 / split árvores | [`docs/findings/MESH_PIPELINE_FINDINGS.md`](docs/findings/MESH_PIPELINE_FINDINGS.md) |
| VRAM / vramd ops | [`docs/MODEL_FINDINGS.md`](docs/MODEL_FINDINGS.md), [`docs/findings/UMS_VRAM_FINDINGS.md`](docs/findings/UMS_VRAM_FINDINGS.md) |
| Retarget Quaternius | [`docs/findings/ANIMATOR_RETARGET_FINDINGS.md`](docs/findings/ANIMATOR_RETARGET_FINDINGS.md) |
| Text-to-motion → SkinTokens (`apply-rigged`) | [`docs/findings/MOTION3D_FINDINGS.md`](docs/findings/MOTION3D_FINDINGS.md) · [`Motion3D/`](Motion3D/) |
| Compressão GLB | [`docs/GLB_FINISH_COMPRESSION.md`](docs/GLB_FINISH_COMPRESSION.md) |
| Testes / cobertura | [`docs/TESTING.md`](docs/TESTING.md) |
| Índice findings | [`docs/findings/README.md`](docs/findings/README.md) |
| Pacote concreto | `<Package>/AGENTS.md` (ex. `GameAssets/`, `Text3D/`, `VibeGame/`) |

## Repository Overview

Monorepo for game-dev AI tools: text-to-image, text-to-3D, text-to-audio, textures, skymaps, PBR map generation, rigging, animation, asset batching, and a browser 3D engine. Primarily Python with one Rust crate (Materialize) and one TypeScript package (VibeGame).

**Key directories:**

| Directory | Language | Package name | Description |
|-----------|----------|--------------|-------------|
| `Shared/` | Python | `aigamekit-shared` | Shared lib (logging, GPU, subprocess, installers, CLI) |
| `Text2D/` | Python | `text2d` | Text-to-image (FLUX SDNQ) |
| `Text2Icon/` | Python | `text2icon` | Text-to-icon (Sana Sprint 0.6B, NVlabs/Sana); transparent BG via rembg |
| `Text3D/` | Python | `text3d` | Text-to-3D (Hunyuan3D-Omni SDNQ) |
| `Paint3D/` | Python | `paint3d` | 3D texturing (Hunyuan3D-Paint 2.1, bilateral smooth, bake_exp=6) |
| `Part3D/` | Python | `part3d` | Semantic mesh part decomposition (Hunyuan3D-Part: P3-SAM + X-Part; SDNQ) |
| `GameAssets/` | Python | `gameassets` | Batch asset generation |
| `Texture2D/` | Python | `texture2d` | Seamless 2D textures (local SD1.5) |
| `Skymap2D/` | Python | `skymap2d` | 360-degree skymaps (local FLUX.1-dev + LoRA) |
| `Text2Sound/` | Python | `text2sound` | Text-to-audio (Stable Audio 3 Small: music/sfx; legado Open via `--model`) |
| `Rigging3D/` | Python | `rigging3d` | Auto-rigging (SkinTokens, Python 3.13) |
| `Animator3D/` | Python | `animator3d` | Animation (bpy 5.2 LTS, Python 3.13); `game-pack` (rigged → animated GLB); clip commands `run`, `jump`, `fall` |
| `Motion3D/` | Python | `motion3d` | Text-to-motion (HY-Motion-1.0 Lite/Full) → NPZ @30fps; `apply-rigged` → SkinTokens via Animator3D `hml22`; vramd |
| `AiGameKitLab/` | Python | `aigamekit-lab` | Debug 3D, benches, profiling |
| `Materialize/` | Rust | `materialize-cli` | PBR map generation (wgpu compute) |
| `Terrain3D/` | Python | `terrain3d` | AI terrain generation via diffusion (terrain-diffusion; vendored; CUDA GPU) |
| `Rocks3D/` | Python | `rocks3d` | Procedural 3D rock generation (no PyTorch) |
| `Vramd/` | Python | `modelserver` (CLI `vramd`/`vramd`) | Unified Model Server (vramd) — single-process GPU/VRAM supervisor |
| `VibeGame/` | TypeScript | `vibegame` (npm) | 3D game engine (bitecs, Three.js, Vite build; Bun tests); `gltf-anim` plugin; `PlayerGLTF` recipe |

All Python packages depend on `aigamekit-shared` (install Shared first). VibeGame is standalone (Bun + Vite); it does not use `aigamekit-shared`.

**Upstream references:** [Materialize](https://github.com/BoundingBoxSoftware/Materialize) (Bounding Box Software) — see `Materialize/README.md`. [VibeGame](https://github.com/dylanebert/vibegame) (dylanebert) — see `VibeGame/README.md`. Root `README.md` / `README_PT.md` have a **References** section.

## Build / Lint / Test Commands

### Full CI (run before PRs)

```bash
make check
```

This runs: lint + format check + typecheck + all **Python** tests and **Materialize** (`cargo test`). It does **not** run VibeGame (Bun/TypeScript); use `make test-vibegame` and related targets in `VibeGame/`.

### Lint

```bash
make lint              # ruff check . + cargo clippy (Materialize/)
ruff check .           # Python linting only
ruff check . --fix     # Auto-fix lint issues
```

### Format

```bash
make fmt               # ruff format . + cargo fmt (Materialize/)
make fmt-check         # Check formatting without writing
ruff format .          # Format Python only
ruff format --check .  # Check Python formatting only
```

### Type checking

```bash
make typecheck         # mypy on Shared/src (--ignore-missing-imports)
```

### Tests — all packages

```bash
make test              # pytest all Python packages + cargo test Materialize
```

### Tests — single package

```bash
make test-shared       # pytest Shared only
make test-modelserver  # pytest ModelServer (vramd) only
make test-text2d       # pytest Text2D only
make test-text2icon    # pytest Text2Icon only
make test-text3d       # pytest Text3D only
make test-paint3d      # pytest Paint3D only
make test-part3d       # pytest Part3D only
make test-gameassets   # pytest GameAssets only
make test-texture2d    # pytest Texture2D only
make test-skymap2d     # pytest Skymap2D only
make test-text2sound   # pytest Text2Sound only
make test-aigamekitlab   # pytest AiGameKitLab only
make test-rigging3d    # pytest Rigging3D only
make test-animator3d   # pytest Animator3D only
make test-motion3d     # pytest Motion3D only
make test-materialize  # cargo test in Materialize/
make test-terrain3d    # pytest Terrain3D only
make test-rocks3d      # pytest Rocks3D only
make test-vibegame     # bun install (frozen) + bun test in VibeGame/
```

Coverage floor (≥100 cases/tool), suite naming, and CPU-first rules:
[`docs/TESTING.md`](docs/TESTING.md) · [Português](docs/TESTING_PT.md).

Animator3D CLI (see [`docs/ANIMATOR3D_AFTER_RIG.md`](docs/ANIMATOR3D_AFTER_RIG.md)):

```bash
animator3d list-animations   # catálogo UAL1/UAL2 (--pack quaternius|quaternius2|both; --json; sem bpy)
animator3d game-pack rigged.glb animated.glb --preset humanoid
animator3d run
animator3d jump
animator3d fall
```

### VibeGame (TypeScript / Bun)

From repo root (requires [Bun](https://bun.sh/) on `PATH`):

```bash
cd VibeGame && bun install --frozen-lockfile   # install deps
make test-vibegame    # tests (runs install first)
make check-vibegame   # tsc --noEmit
make lint-vibegame    # eslint
make fmt-vibegame     # prettier --write
make fmt-check-vibegame  # prettier --check
make build-vibegame   # vite build
```

Formatting: Prettier (`make fmt-vibegame` / `make fmt-check-vibegame`, or `bun run format` / `bun run format:check` in `VibeGame/`).

**Unified installer (CLI on PATH):** from monorepo root, with **Bun** and **Node** available:

```bash
./install.sh vibegame
# or: python3 -m aigamekit_shared.installer.unified vibegame
```

This runs `bun install --frozen-lockfile` and `bun run build` in `VibeGame/`, then installs `vibegame` into `~/.local/bin` (wrapper → `scripts/vibegame-cli.mjs`). Subcommands: `vibegame create <name>`, `vibegame --version`.

**GLB handoff (Text3D / Paint3D / GameAssets → browser):** `loadGltfToScene`, `loadGltfAnimated`, or `loadGltfToSceneWithAnimator` from `vibegame` (`VibeGame/src/extras/gltf-bridge.ts`); declarative `<GLTFLoader url="…">` or `<PlayerGLTF model-url="…">` (`VibeGame/src/plugins/gltf-xml/`, player recipe). Clips: `GltfAnimator` (`VibeGame/src/extras/gltf-animator.ts`); ECS plugin `gltf-anim` optional. Equirect sky → PMREM: `applyEquirectSkyEnvironment` (`VibeGame/src/extras/sky-env.ts`). Pack: `gameassets handoff --public-dir …` (prefers animated GLB when present). Layout: [`docs/MONOREPO_GAME_PIPELINE.md`](docs/MONOREPO_GAME_PIPELINE.md). Examples: [`VibeGame/examples/hello-world/`](VibeGame/examples/hello-world/) (minimal), [`VibeGame/examples/simple-rpg/`](VibeGame/examples/simple-rpg/) (full pipeline). Animator3D: [`docs/ANIMATOR3D_AFTER_RIG.md`](docs/ANIMATOR3D_AFTER_RIG.md). AI: [`docs/ZERO_TO_GAME_AI.md`](docs/ZERO_TO_GAME_AI.md).

Declarative GLB player in world XML:

```html
<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
```

**Idea-to-game (`gameassets dream`):** `gameassets dream "description" --dry-run` calls an LLM to plan assets+scene, emits `game.yaml`/`manifest.csv`/`world.xml`/`main.ts`/`index.html`, runs batch+sky+handoff, and scaffolds a playable Vite project. Pipeline stages (3D, rig, parts, animate) are auto-detected from manifest columns and `game.yaml` profile blocks. Use `--no-animate` or `--no-rig` to opt out. Source: `GameAssets/src/gameassets/dream/` (planner, emitter, runner, llm_context, planlint). Providers: `--llm-provider openai|huggingface|ollama|stdin` (ollama = local, zero-key). `--dry-run` generates files without GPU. Plans are linted + auto-repaired (`planlint`), cached by description+flags (`--replan` to force; env `AIGAMEKIT_DREAM_CACHE`), and carry provenance (`source`/`seed`/`repairs`). `--seed N` pins deterministic generation. Iterate: `gameassets dream refine plan.json "add a dragon"` (LLM edits the plan, `.bak` backup, seeds preserved, regenerates batch files); audit: `gameassets dream explain plan.json [--json]` (exit 1 on lint errors — CI-ready).

### Tests — single test file or test class

```bash
# From inside the package directory (with venv active):
pytest tests/test_env.py                          # Single file
pytest tests/test_env.py::TestEnsurePytorchCudaAllocConf  # Single class
pytest tests/test_env.py::TestEnsurePytorchCudaAllocConf::test_sets_if_empty  # Single test
pytest -k "test_name_pattern"                     # By keyword
pytest -v --tb=short                              # Verbose with short tracebacks
pytest --cov=src --cov-report=html                # With coverage

# Rust (Materialize):
cargo test --manifest-path Materialize/Cargo.toml
cargo test --manifest-path Materialize/Cargo.toml test_preset_roundtrip  # Single test
```

### Install dev dependencies

```bash
cd Shared && pip install -e ".[dev]"   # Per-package dev install
pip install pre-commit && make install-hooks  # Pre-commit hooks
```

## Python Code Style

### Formatting rules (enforced by ruff)

- **Target version:** Python 3.13+
- **Line length:** 120 characters max
- **Quotes:** Double quotes (`"..."`)
- **Indentation:** 4 spaces
- **Line endings:** LF
- **Trailing whitespace:** trimmed
- **Final newline:** required

### Ruff rule set

Config: `ruff.toml` at repo root. Selected rules: `E`, `F`, `W`, `I` (isort), `UP`, `B`, `SIM`, `RUF`.

### Imports

```python
from __future__ import annotations  # Always first

import os
import sys
from pathlib import Path
from typing import Any

from aigamekit_shared.env import get_tool_bin
from aigamekit_shared.logging import Logger
```

- Always use `from __future__ import annotations` as the first import.
- Stdlib imports first, then third-party, then local (enforced by `I`/isort rule).
- Use lazy imports for heavy dependencies (`torch`, `diffusers`) to allow importing without GPU deps.
- Never use wildcard imports.

### Type hints

- Required for `aigamekit-shared` package (`disallow_untyped_defs = True` in `mypy.ini`).
- Use modern syntax: `str | None` (not `Optional[str]`), `list[str]` (not `List[str]`).
- Use `from __future__ import annotations` to enable forward-reference syntax on Python 3.13.
- `Any` is acceptable for dynamic/generic objects (e.g., pipeline objects from diffusers).

### Naming conventions

- **Modules:** `snake_case` (e.g., `subprocess_utils.py`, `cli_rich.py`)
- **Packages:** `lowercase` (e.g., `text2d`, `aigamekit_shared`)
- **Classes:** `PascalCase` (e.g., `KleinFluxGenerator`, `RunResult`)
- **Functions/methods:** `snake_case` (e.g., `get_gpu_info`, `format_bytes`)
- **Constants:** `UPPER_SNAKE_CASE` (e.g., `DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB`)
- **Private helpers:** prefix with `_` (e.g., `_torch()`, `_model_id()`)
- **CLI entry points:** `cli.py` or `cli_rich.py` in the package, `__main__.py` for `python -m` support.

### Docstrings

Google-style docstrings:

```python
def resolve_binary(env_name: str, default_name: str) -> str:
    """Resolve executable: env var -> PATH -> FileNotFoundError.

    Args:
        env_name: Environment variable name (e.g., ``TEXT2D_BIN``).
        default_name: Command name on PATH (e.g., ``text2d``).

    Returns:
        Absolute path or executable name found.

    Raises:
        FileNotFoundError: Binary not found.
    """
```

### Error handling

- Raise specific exceptions with clear messages.
- Use `from None` to suppress irrelevant chained tracebacks, `from e` to preserve cause.
- Use `contextlib.suppress(ValueError)` for expected non-critical failures.
- Return tuples `(success, message)` for validation-style functions.
- Use dataclasses for structured results (e.g., `RunResult`).

### Testing conventions

- Framework: **pytest** with `pytest-cov` (Python); **cargo test** (Materialize); **bun test** (VibeGame).
- Test file location: `<Package>/tests/test_<module>.py`.
- Test class organization: group by tested feature in classes (`class TestFeatureName:`).
- Use `capsys` fixture for stdout/stderr assertions.
- Use `unittest.mock.patch` and `patch.dict(os.environ, ...)` for env isolation.
- GPU-dependent tests should auto-skip without CUDA (use `pytest.importorskip` or guards).
- Config in each `pyproject.toml`: `pythonpath = ["src"]`, `testpaths = ["tests"]`.
- **Coverage floor:** every installer tool + accessory keeps **≥100** collected automated cases. Prefer CPU-first suites named `test_*_coverage_*.py` (VibeGame: `tests/coverage-100.test.ts`; Materialize: `#[cfg(test)]` in `src/`). Full guide: [`docs/TESTING.md`](docs/TESTING.md).
- Lazy-import heavy deps (`torch`, `diffusers`) **inside** tests so pytest collection stays cheap.
- Do not add fluff “pad” tests that only assert source trees are non-empty.

## Rust Code Style (Materialize/)

- **Edition:** Rust 2021
- **Error handling:** `anyhow::Result` for application code.
- **CLI:** `clap` with derive macros.
- **Formatting:** `cargo fmt` (standard rustfmt).
- **Linting:** `cargo clippy -- -D warnings`.
- **Tests:** Inline `#[cfg(test)] mod tests` for unit tests; `Materialize/tests/` for integration tests.
- **Naming:** `PascalCase` for types/enums, `snake_case` for functions/variables, `SCREAMING_SNAKE` for constants.
- Use `bytemuck` for GPU buffer casting (`Pod`, `Zeroable`).
- All CLI args documented with `#[arg(help = "...")]`.

## Package Structure (Python)

Each Python package follows this layout:

```
<PackageName>/
  pyproject.toml          # PEP 621 metadata, deps, pytest config
  setup.py                # Legacy installer compatibility (optional)
  src/
    <package_name>/
      __init__.py
      __main__.py         # python -m <package_name>
      cli.py              # Click CLI
      cli_rich.py         # Rich-enhanced CLI
      generator.py        # Core logic (example)
  tests/
    __init__.py
    test_<module>.py
  scripts/
    installer.py          # Package installer (uses aigamekit-shared)
```

## AiGameKitLab — Debug & Mesh Comparison

The monorepo includes **AiGameKitLab** (`aigamekit-lab`) for GLB debugging, inspection, and automated comparison. It is the primary tool for verifying mesh quality in the pipeline.

### Key Commands

| Command | Purpose |
|---------|---------|
| `aigamekit-lab debug screenshot <glb> -o <dir>` | Multi-angle PNG screenshots (4 views default) |
| `aigamekit-lab debug compare <a.glb> <b.glb> --struct-diff --image-metrics` | Side-by-side structural + visual comparison |
| `aigamekit-lab debug inspect <glb>` | JSON metadata dump (mesh, armature, animation, materials) |
| `aigamekit-lab debug bundle <glb> -o <dir>` | Full bundle: inspect + screenshots + bundle.json |
| `aigamekit-lab debug viz <glb> -m <mode>` | Mesh-debug viz: `normals`, `normals-arrows`, `orientation`, `uv`, `edges`, `weights` (dominant/count/unweighted/bone); `--wireframe` overlay |
| `aigamekit-lab check glb <glb> <rules.yaml>` | Validate GLB against JSON/YAML rules (CI-ready, exit 0/1) |

### Mesh Comparison Workflow

```bash
# 1. Capture screenshots for baseline
aigamekit-lab debug screenshot before.glb -o baseline/

# 2. After changes, capture new screenshots
aigamekit-lab debug screenshot after.glb -o after/

# 3. Automated structural + visual comparison
aigamekit-lab debug compare before.glb after.glb \
  --image-metrics \
  --fail-below-ssim 0.85
```

`--struct-diff` is on by default and generates an `inspect_diff` section inside `diff_report.json` with per-view vertex/face counts. `--image-metrics` adds MAE, RMSE, and SSIM scores. The `--fail-below-ssim` flag exits with code 1 if any view falls below the threshold, useful in CI or pre-commit hooks.

### Render Options

| Flag | Effect |
|------|--------|
| `--engine workbench\|eevee` | Render engine selection |
| `--ortho` | Orthographic camera |
| `--no-transparent-film` | Opaque background |
| `--views 4` | Number of evenly-spaced camera angles |

### Requirements

Requires `bpy` installed in the active venv (`pip install bpy`, Python 3.13+ / Blender 5.2 LTS). All rendering is **native bpy** — no `animator3d` subprocess. Weight heatmaps (`debug inspect-rig --show-weights`), turntable GIFs (`debug turntable`), material inspection (`debug inspect-material`), and overlay diffs (`debug compare --overlay`) are handled in-process.

See `AiGameKitLab/README.md` for full documentation.

## vramd (VRAM Coordination)

The canonical system is the **vramd** supervisor in `Vramd/` (PyPI package,
installed by `./install.sh vramd`) — one process, one socket
(`~/.cache/vramd/vramd.sock`), 9 GPU backends, smart job queue (priority + VRAM
affinity cuts≤3), and weight+LRU eviction. It replaces the former
`ModelServer/`. Client helpers live in
`Shared/src/aigamekit_shared/vramd_client.py`.
CLI: `vramd` (`vramd start` / `status` / `queue` / `cancel` / `respawn` / …).

**File logs:** all Python tools + vramd write
`~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log` via `aigamekit_shared.logging`
(`configure_logging` / `Logger`). vramd always files `_log` lines; console needs
`-v`. Guide: [`docs/LOGGING.md`](docs/LOGGING.md).

**Architecture:** CLIs call `try_vramd_delegation` / `delegate_to_vramd` **before** any
in-process GPU prep (auto-starts vramd unless `VRAMD_AUTO_START=0`). Jobs go
through `JobQueue` → `AffinityScheduler` → `WorkerPool` (`MAX_INFLIGHT=1`).
Interactive CLI beats batch (`VRAMD_PRIORITY=batch` set by GameAssets).
Per-tool legacy servers (`text2icon server`, etc.) remain as **deprecated** fallback only.

**Canonical venv + subprocess workers (live):** vramd runs in `Vramd/.venv`
(`./install.sh vramd`). Auto-start precedence
(`_resolve_vramd_start_cmd`): `VRAMD_BIN` → `Vramd/.venv/bin/python`
→ `vramd` on PATH → `sys.executable` (last resort, warns).
The auto-start sets `VRAMD_TOOLS_ROOT` (checkout) and `VRAMD_BACKENDS_FILE`
(`Shared/src/aigamekit_shared/data/backends.yaml`).
Each GPU backend = persistent worker in `<Tool>/.venv` (JSONL stdin/stdout).
Design: [`docs/UMS_SUBPROCESS_PLAN.md`](docs/UMS_SUBPROCESS_PLAN.md) (Fases 0–4 ✅,
histórico — o daemon é agora o vramd).
Rollback: `VRAMD_SUBPROCESS=0` (não suportado sem adapters in-process).
After editing tool code: `vramd respawn
<backend>` (not a full vramd restart). GameAssets waves:
[`docs/GAMEASSETS_UMS_BATCH.md`](docs/GAMEASSETS_UMS_BATCH.md).

**VRAM coordination:** **vramd + hw-auto** are the public VRAM authority. No
operator CLI `--low-vram` / `--memory-efficient` — hw-auto fills peak signals on
the vramd payload (`sdnq_preset` / `memory_efficient` via `with_vramd_peak_opts` /
`vramd_batch.resolve_*_vram_opts`). Omit those → admit assumes fp16 (~8 GiB
text3d) → refuse on 6 GB. Call `try_vramd_delegation` **before** GPU prep;
`prepare_gpu_exclusive` only after vramd fail / `--no-vramd`. Multi-GPU: put
`gpu_ids` in the payload (`with_vramd_load_opts`) or `--gpu-ids` only applies
in-process. Legacy per-tool / blind `ensure_vram`: **opt-in**
`VRAMD_ALLOW_LEGACY_SERVER=1`. Kill refuses while vramd busy. Free VRAM:
**NVML-first** (`aigamekit_shared.gpu`, dep `nvidia-ml-py`). Ops:
[`docs/MODEL_FINDINGS.md`](docs/MODEL_FINDINGS.md),
[`docs/findings/UMS_VRAM_FINDINGS.md`](docs/findings/UMS_VRAM_FINDINGS.md),
[`docs/GAMEASSETS_UMS_BATCH.md`](docs/GAMEASSETS_UMS_BATCH.md).

### Agents — VRAM busy checklist (do NOT skip)

1. `vramd status` / `queue` / `doctor` — see **HOLDING** / who owns the GPU.
2. Wait (`vramd wait <job_id>`, tool with `--vramd-stream`) or `vramd cancel <job_id>`.
3. **Never** `kill` / GPU pkill / `--gpu-kill-others` while vramd has jobs —
   that races the queue and can murder the wrong workload (bench, sibling tool, batch).
4. Idle vramd holding VRAM (live workers keep ~0.3–1 GiB CUDA context each) so
   `free < peak` (ex. text3d int4 ~4991 MiB): **`vramd zero`** — kills all idle
   workers without stopping the supervisor (refused `ZERO_BUSY` when the queue
   is busy). The supervisor itself no longer creates a CUDA context
   (`clear_cuda_memory`/`torch_reserved_mib` skip torch calls when CUDA is not
   initialized); only a supervisor started *before* this fix needs one last
   `vramd stop` + auto-start.
5. Only use `--no-vramd` + in-process when you intentionally bypass the supervisor;
   then kill is still refused if vramd is busy.
6. Full guide: `Vramd/README.md` (section *Agents / anti-patterns*).

**Commands:**
```bash
vramd start|stop|status|submit|queue|wait|cancel|flush|backends|preload|evict|reap|respawn|zero|stats|debug|bench|doctor|calibrate
# cancel <job_id|prefixo> | cancel --all | flush [--queued-only]
# respawn <backend|--all> [--hot]  — reinicia SÓ o worker da tool (código novo), sem reiniciar o supervisor
# zero                        — zera TODA a VRAM do vramd (mata workers idle) SEM parar o supervisor
# calibrate <backend>         — mede o footprint VRAM real (job real + NVML por processo) e emite o descriptor YAML
# same as: vramd …
text2icon generate "icon" -o out.png   # Auto-delegates to vramd (~7s vs ~20s cold)
text2icon generate "icon" -o out.png --vramd-stream --vramd-priority interactive
# Tool flags (all GPU generate/decompose): --vramd-priority | --no-vramd | --vramd-stream
# Deprecated legacy: text2icon server | server-status | server-stop
```

**Calibration is tied to VRAM.** The packaged catalog lives in
`Shared/src/aigamekit_shared/data/calibrated/` — one file per GPU capacity
(`backends-6g.yaml`, `backends-16g.yaml`…), generated with `vramd calibrate` on
a GPU with that VRAM. On auto-start, `ensure_vramd_running` picks the file with
the **largest label ≤ the GPU's total VRAM** (a 6 GB card uses the 6 GB
calibration; a 24 GB card with only 16 GB calibrated uses the 16 GB one — the
most restrictive measured scenario, for safety) and passes it as an overlay
(`VRAMD_BACKENDS_FILE=base:calibrated`, per-key merge). If no calibration
exists for the user's hardware (e.g. a GPU smaller than every file), the system
falls back to **estimates + hw-auto** — `vramd calibrate <backend> --out
data/calibrated/backends-<N>g.yaml` on real hardware produces a new catalog
entry.

**Key APIs in `aigamekit_shared.vramd_client`:**
| Function | Purpose |
|----------|---------|
| `delegate_to_vramd(backend, request)` | Sync generate via vramd (main CLI path) |
| `submit_to_vramd` / `poll_vramd_job` / `wait_vramd_job` / `cancel_vramd_job` | Async job API |
| `respawn_vramd_backend(backend, lazy=True)` | Restart a tool's worker subprocess (pick up edited tool code without restarting the supervisor) |
| `zero_vramd_vram()` | Zero ALL vramd-held VRAM (kills idle workers + scrub) without stopping the supervisor; `None` when vramd is down |
| `fetch_vramd_queue_snapshot` / `vramd_is_busy` / `format_vramd_holding_summary` | Queue introspection |
| `VRAMD_DO_NOT_KILL_TIP` | Stable tip string for CLIs/agents |
| `ensure_vram_available(needed_mib)` | Ask vramd; legacy sockets only if `VRAMD_ALLOW_LEGACY_SERVER=1` |
| `ensure_vramd_running()` | Auto-start vramd supervisor |
| `discover_server_pids()` | Protect server PIDs from GPU kill |
| `ModelServer(...)` | Legacy per-tool server class (deprecated) |

**Env vars:** `VRAMD_CLIENT_SOCKET`, `VRAMD_AUTO_START`,
`VRAMD_PRIORITY`, `VRAMD_STREAM`, `VRAMD_DEBUG`,
`VRAMD_MAX_AFFINITY_CUTS`, `VRAMD_MAX_QUEUE_DEPTH`,
`VRAMD_MAX_INFLIGHT`, `AIGAMEKIT_ALLOW_LEGACY_SERVER`, `VRAMD_BIN`,
`AIGAMEKIT_PREFER_MONOREPO` (default on — `resolve_binary` prefers
`<Tool>/.venv/bin`). WAL: `~/.cache/aigamekit/vramd-jobs.jsonl`.
Dev edits under `*/src/` are live (editable install); tool worker reload →
`vramd respawn <backend>`; supervisor/protocol → `vramd stop`. See
`Vramd/README.md`, [`docs/INSTALLING.md`](docs/INSTALLING.md).

## Commit Conventions

Use Conventional Commits:

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Code style (formatting, no logic change)
- `refactor:` Code refactoring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

## CI Pipeline

CI runs on push/PR to `main` (`.github/workflows/ci.yml`):

1. **lint:** ruff check + ruff format --check + pre-commit (includes **mypy** on Shared — ruff green ≠ lint green)
2. **test-python:** pytest matrix on Python 3.13 (Shared, GameAssets, Texture2D, Skymap2D, Rigging3D, Text2Sound, AiGameKitLab, Rocks3D, Animator3D) — each job installs `Shared/.[dev]` first
3. **test-rust:** cargo fmt --check + cargo clippy + cargo test (Materialize; continue-on-error)
4. **vibegame:** Bun check + eslint + prettier + test + vite build (root workflow; not only `VibeGame/.github/`)

Excluded from the Python matrix (heavy PyTorch / diffusers / GPU stacks): Text2D, Text3D, Paint3D, Part3D, Terrain3D, ModelServer.

CI pitfalls (softfill sem Text3D, pedalboard SIGILL, Shared `[dev]` mesh deps, VibeGame flakes): [`docs/TESTING.md`](docs/TESTING.md) · [`docs/TESTING_PT.md`](docs/TESTING_PT.md).

## Important Notes

- Pre-commit (ruff + mypy) does **not** run VibeGame ESLint/Prettier; use `make lint-vibegame` / `make fmt-check-vibegame` locally or rely on the root **vibegame** CI job.
- Shared `[dev]` must keep `numpy` / `scipy` / `trimesh` so `mesh_repair*` tests collect on CI (`pip install -e Shared/.[dev]`).
- Do NOT modify vendored code in `Paint3D/src/paint3d/hy3dpaint/`, `Paint3D/src/paint3d/hunyuan3d-2.1/`, or `Rigging3D/src/rigging3d/skintokens/` — these are excluded from lint.
- Shared must be installed before any other package: `cd Shared && pip install -e .`
- Each package may have its own `.venv/` — tests should use the package-local venv.
- Environment variables are the primary configuration mechanism (see README.md "Environment variables" section).
- Run `make check` before considering work complete.
- **Git workflow: trabalha sempre diretamente no `main` — NÃO criar branches.** Não usar `git checkout -b`, `git switch -c`, `git worktree`, nem qualquer fluxo de feature-branch. Todas as alterações (commits, edits) são feitas sobre `main`. O utilizador gere merges/integração manualmente; o agente não deve criar ramos paralelos. Exceção só se o utilizador pedir explicitamente um branch numa tarefa específica.
- **Game asset master pipeline (compressão GLB)**: meshopt + KTX2 **ON** por
  defeito (`gltf_transform_finish`, bake-master, lod finish). Meshopt: bpy 5.2+
  (`export_meshopt_compression_enable`; Linux: `libmeshoptimizer-dev`) ou
  gltf-transform. KTX2/UASTC: Node + `npx @gltf-transform/cli` **e** CLI `ktx`
  (KTX-Software; `./install.sh text3d` → `~/.local/opt/KTX-Software`).
  Re-comprimir: `text3d finish asset.glb`. Verifica: `text3d doctor`.
  Happy path: [`docs/GLB_FINISH_COMPRESSION.md`](docs/GLB_FINISH_COMPRESSION.md).
  Sem deps → fallback gracioso; `aigamekit-lab check glb` pode falhar
  `texture_format: ktx2` / `compression: meshopt`.

## Learned User Preferences

- Prefere explicações e pedidos de funcionalidade em português ao trabalhar neste repositório.
- **Git: NÃO criar branches — trabalhar sempre no `main`.** O utilizador quer commits diretos sobre `main`; gere merges/integração de trabalhos paralelos manualmente. O agente nunca deve abrir feature-branches (`git checkout -b`, etc.) a menos que o utilizador peça explicitamente.
- Para `vibegame run`: instalação no **app** deve poder ser opcional quando `node_modules` já está completo; na **engine**, o CLI pode correr `bun install` automaticamente se faltarem dependências declaradas no `package.json` (evita falhas de build por módulos em falta); usar `--skip-engine-install` ou `--skip-install` para pular esse passo quando o ambiente já está sincronizado.
- Spawner e conteúdo declarativo no VibeGame: manter o mesmo estilo de recipes/parsers/XML em `index.html` já usado no projeto.
- Spawner: diferenciar objetos estáticos (árvores, props) de dinâmicos (caixas empurráveis, inimigos em movimento, etc.) e usar perfis que definam defaults automáticos por tipo de objeto.
- Ajustes de spawn e terreno: priorizar soluções que não degradem muito a performance do mapa.
- Lógica por objeto no VibeGame: scripts ao nível da **entidade** (estilo MonoBehaviour no modelo); expor via atributo `script` no recipe que cria a entidade (ex. `GLTFLoader`) ou filho `<MonoBehaviour>` com merge, em linha com o plugin `MonoBehaviour`.
- VibeGame: correções reutilizáveis devem ir para a **engine**; no **jogo/exemplo** (ex. `simple-rpg`) ficam só ajustes específicos desse jogo — incluindo alinhar `index.html`/manifests aos assets em `public/` (não referenciar `_intermediate` no runtime); estabilizar **LOD0** antes de introduzir um sistema de LOD dinâmico completo.
- Exemplos como `simple-rpg`: pós-processamento e partículas com intensidade moderada para jogabilidade, mesmo quando os efeitos permanecem ativos para teste; para regressões visuais/jogo, preferir que o agente **itere no browser** (MCP Chrome/DevTools ou Playwright) em vez de só listar passos manuais.
- Áudio 3D e SFX de movimento: preferir integração via sistema da engine (XML/recipes, `AudioListener` + câmera principal) e alinhar o som à ação; evitar silêncio inicial longo **e caudas ~20–30 s** nos WAV (trim na geração / `regen_sounds.py`); whoosh/hit de melee no **pico do clip (~35%)**, não no key-edge; QA com `?profiler=audio` / `__VIBEGAME__.audio`. Detalhe: [`docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md`](docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md), [`VibeGame/docs/AUDIO.md`](VibeGame/docs/AUDIO.md).
- Instalações e retries de ferramentas devem ser **não-interativos**: `install.sh`, instaladores por pacote e loops de correção não devem bloquear à espera de input (ex. prompts de licença, confirmações); quando a automação falha, reportar e seguir em vez de ficar preso num retry interativo. Em `install.sh --all` priorizar reuso de cache (pip/uv) e alinhamento de versões entre pacotes irmãos para reduzir downloads/builds redundantes.
- Pipeline de game assets deve tratar o **LOD0 como o asset final entregável** com autodeteção do estágio terminal: se o asset tem animação, o GLB animado vira LOD0; se tem rig sem animação, o rigged vira LOD0; se só chega ao paint, o LOD0 é decimado do painted para 1.2x `target_faces` da categoria (com tangents+KTX2+meshopt via `--finish-lod0`). LOD0 sem rig/animação quando o asset os tem é regressão.
- CLIs de progresso e dashboards do `gameassets batch` devem refletir **todas as stages** do pipeline (LOD, rig, animate, validate) e não parar visualmente no paint quando há mais trabalho pela frente.

## Learned Workspace Facts

- Terreno em exemplos VibeGame pode parecer voxel/escadinha; amostragem de altura/normal com um único ponto tende a falhar — estratégias multi-amostra ou suavização costumam ser necessárias para alinhar props ao chão. Árvores/erva a flutuar: (1) pivô GLB no centro em vez da base (pipeline Text3D/GameAssets → origem nos pés por omissão); (2) mesh leaf densificado vs spawn grosso — `meshSurfaceResolutionForPoint` tem de usar `maxBoostOverAabb` do deepest leaf (igual ao chunk), não só `boostAt(ponto)` (floats esparsos em dunas/pad skirt); ver `terrain`/`spawner` `context.md`. No recipe `<Terrain>`, campos kebab-case; `collision-resolution` (32/64/128) no `TerrainLOD` sem ser sobrescrito pela `resolution` do chunk.
- **VibeGame chão:** estáticos (`tree`/`foliage`/`place`) = AABB + `TerrainSpawned` + resync após pad/road/river. Dinâmicos (`creature` / `role=enemy|npc`) = spawn seed Y + **CCT** no heightfield; perfil `groundAlign: none`, `baseYOffset: 0`. Scripts = AI/anim só — sem lift/snap/BVH/`settle`. `goblin_collision.glb` unused. Docs: [`docs/findings/VIBEGAME_SPAWN_GROUND_FINDINGS.md`](docs/findings/VIBEGAME_SPAWN_GROUND_FINDINGS.md), `VibeGame/src/plugins/spawner/context.md`.
- **VibeGame rigidbodies: só TOP-LEVEL com `place`.** O `TerrainPlaceSystem` escreve o mundo em Transform E Rigidbody; entidades parentadas (`<Group pos≠0>` + filhos com `pos` local) NUNCA espelham o mundo no body Rapier (rb fica 0,0,0 → herói cai através do objeto). Para salas/estruturas: `<TerrainPad>` achata o terreno (altura = terreno no centro) + entidades top-level com `place="at: X Z"` — tudo cai no plano do pad. Dentro do core plano do pad, o placement ancora no plano analítico (`sampleTerrainSurfaceMatrix` → `padPlane: true` → `TerrainPlaceSystem` usa `s.worldY`); sem isso a amostragem do mesh lattice (LOD grosso à distância) mistura a borda do pad com o terreno e afunda/flutua props até ~1 m. Regressão coberta por `tests/unit/spawner/pad-plane-anchor.test.ts`. Exemplo: `examples/simple-rpg/public/world/interiors.xml` (3 salas em zona remota, portais de saída `building-portal.ts`).
- O comando `gameassets mesh reorigin-feet` repõe a origem de GLBs estáticos nos pés/base; modelos rigged com animação podem precisar de correção de orientação de root (ex. rotação) antes de centrar o pivô — não aplicar só `reorigin-feet` sem validar o resultado.
- O **rigged GLB** tem de herdar a origem/pivô e a orientação do LOD0 (mesh centrada em X/Z, y=0 na sola dos pés, em pé na vertical correta) e o esqueleto tem de estar alinhado e dentro da malha — não rotacionado, não deslocado em Y para fora do mesh. Helpers de debug (ex. icosphere em 0,0,0, eixos visuais) NÃO devem ficar no GLB final exportado pela stage de rig.
- Text3D / Hunyuan3D (marching cubes): paredes duplas, rachas, edifícios tipo casca-plástico. Reparo: `aigamekit_shared.mesh_repair` perfis `topology_clean` / `pre_decimate_uv` / `part_decode` / `post_voxel`. `topology_clean` actual: weld → slivers/debris → fill → watertight **seletivo** (diâmetro de loop) → shade-smooth; **sem** `force_close_base`/flare/Taubin (removidos — destruíam capela). **Base oca por baixo é OK** — QA `_shape` foca cortes/forma graves, não fechar chão. Refs Text2D eye-level 3/4 (`categories.building` + `prompt_builder`). Paint: `ensure_clean_for_paint` + `paint_prep.restrict_inpaint`. Lições: `docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`.
- **topology-fix rápido (engine arrays)**: `text3d topology-fix --engine arrays|auto|bpy` (default `arrays`; `auto`≡arrays). GameAssets batch/resume passa `--engine arrays`. Filtros `topology_clean` vetorizados em `aigamekit_shared.mesh_repair_arrays` (numpy/scipy) quando o mesh não tem UVs/weights/shape-keys/armature; fill/caps/`make_watertight` ficam em bmesh. Morph-close no engine arrays corre **depois** do weld. Estudo: `docs/TOPOLOGY_FIX_GPU_STUDY.md`.
- **Octree / faces / manifesto:** happy path = `category` + `size_m` (sem `octree_resolution`). `char_m=(L·H·W)^(1/3)` → `bbox_tune` → octree; faces ≈ `8×10⁴·char_m²` ≈ `κ·octree²`. Props: tecto faces (128–160). Terrain/rock: **piso físico** `√(8e4·char²/κ)×1.125` (cobre antigos +32 manuais anti-buracos). Hero dedos = único override típico (`octree`+`mc_level: 0`). Manual: [`docs/MANIFEST_AUTHORING.md`](docs/MANIFEST_AUTHORING.md). Dados: [`docs/findings/OCTREE_FACES_FINDINGS.md`](docs/findings/OCTREE_FACES_FINDINGS.md).
- **vramd ops (residual / singleton / idle):** detalhe em [`docs/findings/UMS_VRAM_FINDINGS.md`](docs/findings/UMS_VRAM_FINDINGS.md) e checklist na secção Model Server acima. Nunca `kill`/pkill GPU com vramd busy; `vramd respawn <backend>` após editar código de tool.
- O comando `vibegame run` foi concebido para rebuild/atualização da engine face a exemplos que usam `file:vibegame`; em Windows podem ocorrer falhas de cópia/cache (`ENOENT` no pacote `vibegame`) e é preciso alvo/cwd coerente com a raiz da engine ou exemplo com `dev` ligado à engine.
- Skymap2D e equirect/PMREM: o modelo HF Flux-LoRA-Equirectangular-v3 devolve imagens em resolução errada (1024×768 em vez do pedido 2048×1024) e com os polos ao centro vertical em vez das bordas; Skymap2D `generator.py` faz auto-resize e shift vertical de 50% para corrigir. O `PMREMGenerator` do Three.js ignora `texture.offset`/`repeat` no shader interno — para ajustar UV de texturas equirect antes de `fromEquirectangular()` é necessário manipular o bitmap a nível de píxeis (canvas). Convenção equirect Three.js: `u = atan(dir.z, dir.x)`, `v = asin(dir.y)` — centro da imagem = horizonte, topo = zénite, fundo = nadir. Texturas equirect em **retrato** (altura > largura) ou com eixos trocados podem mapear o azimute ao eixo vertical do bitmap e produzir artefactos tipo «pilares» no céu; convém normalizar para panorama 2:1 em paisagem antes do PMREM quando isso ocorrer.
- Dependências de screen-space / pós-processamento (ex. `screen-space-reflections`) podem importar símbolos removidos ou renomeados no Three.js (ex. `WebGLMultipleRenderTargets`), falhando no Vite com «No matching export» até alinhar versões do Three ou substituir o efeito. Em áudio Web, `AudioContext` bloqueado ou `listener.positionX` indisponível costuma ligar-se a autoplay sem gesto do utilizador e/ou à ausência de cadeia válida `AudioListener` + câmera principal.
- **VibeGame áudio/combate:** bank com cull espacial (`maxDistance` default 40); caches 2D≠spatial; **`preloadSounds` no browser enfileira até gesto** (`allowSoundPreload` via `<Scene resume-audio-on-user-gesture>` / `resumeAudioContextOnFirstUserGesture`) — Howl no boot = warning autoplay; profiler tab Audio (`?profiler=audio`, `__VIBEGAME__.audio`); SFX one-shot curtos (caudas ~20–30 s = “combate infinito”); impacto melee/harvest em **`ATTACK_IMPACT_FRACTION = 0.35`** (pico Quaternius ~27%, não 0.7). Docs: [`VibeGame/docs/AUDIO.md`](VibeGame/docs/AUDIO.md), [`docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md`](docs/findings/VIBEGAME_AUDIO_COMBAT_FINDINGS.md).
- **VibeGame DevTools noise:** Typr (troika FloatingText) → `unsupported GPOS/GSUB` silenciado por `silenceTyprOpentypeNoise` no plugin `vibegame()`; yoga/`@pmndrs/uikit` WASM no prebundle Vite → Firefox `URL constructor` / `sourceMappingURL: null` — excluir `yoga-layout` + `@pmndrs/uikit` de `optimizeDeps` (plugin faz merge; limpar `node_modules/.vite`). WebGL `DEPTH_ATTACHMENT: Attachment has no width or height` no boot → EffectComposer/shadow a 0×0 durante warmup — `syncComposerSize` + guard no warmup + `shadowMapSize` clamp. Ver [`VibeGame/src/vite/context.md`](VibeGame/src/vite/context.md), [`rendering/context.md`](VibeGame/src/plugins/rendering/context.md).
- **VibeGame world modular:** `<Include src="/world/…">` expande antes do parse (`expandIncludes`, depth≤8, ciclos fail-fast) — browser `fetch`, headless/analyze disco. Plugin `city-layout` (`CityGrid` + Street/Wall/Building; coords célula com **espaço**). Offline: `vibegame analyze index.html` (includes quebrados, assets em falta, overlaps sólidos). Docs: `VibeGame/src/core/xml/context.md`, `city-layout/context.md`, `cli/context.md`; exemplo `simple-rpg/public/world/`.
- O conteúdo sob `<Scene>` no VibeGame é injetado como HTML (`innerHTML`); a tag nativa **`<script>`** não serve para marcar módulos TS do motor — usar atributo `script` nos recipes ou um nome de elemento que não colida com HTML.
- Sem URL de heightmap no terreno, `TerrainLOD` / `@interverse/three-terrain-lod` pode gerar um heightmap procedural internamente; os ficheiros exportados pelo Terrain3D (`terrain.json`, `heightmap.png`, etc.) só têm efeito se o recipe/plugin apontar para eles — atributos XML não suportados podem ser ignorados em silêncio. A geração de heightmaps com **Terrain3D** (difusão) pode exceder a VRAM confortável em GPUs da ordem de **~6 GB**; nesse caso o fluxo típico é gerar noutra máquina com mais VRAM e copiar os artefactos para `public/` do exemplo.
- OpenCode (`opencode.json` no repositório): entradas MCP locais devem declarar `type: "local"` e `command` como array de strings com executável e argumentos (não o par `command` + `args` usado noutras ferramentas).
- VibeGame: corpos dinâmicos GLTF podem ter colisor desalinhado do mesh se o centro do AABB não coincidir com a origem da entidade — definir `Collider.posOffset*` a partir do delta AABB→Transform em espaço local. No plugin de partículas (`three.quarks`), usar o emissor interno `ParticleSystem.emitter`; um wrapper `ParticleEmitter` à parte faz o batch descartar o sistema no update e as partículas deixam de aparecer.
- No PyPI, `bpy>=5.2.0` (LTS) exige Python 3.13. Rigging3D e Animator3D usam stack **3.13 + `bpy>=5.2.0`** — não assumir outro Python/`bpy` para estes pacotes. Meshopt nativo no exporter GLTF (`export_meshopt_compression_enable`); em Linux precisa de `libmeshoptimizer.so` (`libmeshoptimizer-dev`). KTX2/UASTC: `@gltf-transform/cli` **+** binário `ktx` (KTX-Software) — só npx não chega; ver [`docs/GLB_FINISH_COMPRESSION.md`](docs/GLB_FINISH_COMPRESSION.md).
- **QualityEngine** (`aigamekit_shared.quality.QualityEngine`): sistema unificado de presets de qualidade cross-tool. 5 tiers (`fast|low|medium|high|highest`) em `Shared/src/aigamekit_shared/data/quality-profiles.yaml`, 14 categorias de assets + 11 audio_kinds em `asset-categories.yaml`. Todas as tools Python expõem `--quality` (e opcionalmente `--category`): Text2D, Texture2D, Skymap2D, Text3D, Paint3D, Part3D, Text2Sound, Rigging3D, Terrain3D, Motion3D. O QualityEngine faz resolução soft — preenche defaults só quando o utilizador não explicitou o parâmetro (via `ParameterSource`). O GameAssets usa `generation:` no `game.yaml` (mapeia para `--quality`) e passa `--quality`/`--category` às sub-tools. Spec: `docs/superpowers/specs/2026-04-30-quality-presets-design.md`.

- **Arquitetura de responsabilidades — mesh operations**: O **Text3D** é o único dono de operações de mesh (LOD, collision, simplify, remesh, remesh-textured, `topology-fix`, `bake-master`). O GameAssets NÃO deve conter código de mesh — apenas orquestra subprocessos `text3d`. Não usar `bpy` nem `trimesh` diretamente no GameAssets (o legado `bpy_simplify.py` foi removido). O `text3d lod` preserva armatures/animations — no master pipeline Round 3 é ele que gera a ladder rigada (decimate sobre o animated/rigged). `rigging3d transfer-weights` continua disponível como CLI manual (rebinding pontual via `aigamekit_shared.skin_transfer`), mas deixou de fazer parte do DAG do batch.

- **Master pipeline Round 3:** generate → topology-fix → paint → rig(`_painted`) → game-pack×1 → `text3d lod` → collision → validate. LOD0 = animated > rigged > painted. Sem `transfer-weights` no DAG. Detalhe: [`GameAssets/AGENTS.md`](GameAssets/AGENTS.md), [`docs/findings/MESH_PIPELINE_FINDINGS.md`](docs/findings/MESH_PIPELINE_FINDINGS.md).
- **Retarget Quaternius:** nunca compensar eixo/pivô em pós binário do GLB; `loc_conv` + `_bone_rest_dir`; `root` estático nos pés, fora do bone_map. Detalhe: [`docs/findings/ANIMATOR_RETARGET_FINDINGS.md`](docs/findings/ANIMATOR_RETARGET_FINDINGS.md).

- **V/Tri=3 — dois modos**: (A) GLB sem `NORMAL` → import flat → exporter parte loops — `shade_smooth` basta; (B) verts **já duplicados** no ficheiro (comum pós SkinTokens/re-export: `_rigged`/`_animated` V/Tri≈3 enquanto `_painted` ≈0.66) — **weld obrigatório**; shade sozinho não funde. `smooth_shade_scene` = weld (`DEFAULT_PREEXPORT_WELD_DIST` = `3e-4`) + smooth-by-angle, **mas só quando o V/Tri o denuncia** (≥ 2): um shape marching-cubes tem V/Tri≈0.5 e o weld bmesh nele era o passo que fazia o export do shape encalhar; `force_weld=True` ignora a heurística. Acima de 1M verts em meshes sem UV/weights o weld corre em arrays (`weld_mesh_arrays`) para não pagar o BMesh. Round 3 LODa animated/rigged no path geométrico: Decimate sem weld → LOD1/2 moth-eaten (comps≈faces). Path textured (`--painted-mesh` / `mesh_simplify`) já soldava. Detalhe: [`docs/findings/MESH_PIPELINE_FINDINGS.md`](docs/findings/MESH_PIPELINE_FINDINGS.md#vtri3-e-lod-moth-eaten-2026-07).

- **`repair_glb(seal_export=True)` — smooth a 180°, não a 60°**: o fecho watertight só sobrevive ao reimport se o exporter não partir loops. Com `smooth_shade_scene(meshes)` (60°) as creases viram arestas duras, o exporter duplica vértices e o GLB reimportado volta a ter boundary edges (teste `test_repair_glb_watertight_survives_roundtrip`: 48). `degrees=180.0` mantém `NORMAL` no ficheiro (sem viewer flat) sem split. Omitir `NORMAL` (`export_normals=False`) resolvia o boundary mas reintroduzia o V/Tri=3 no próximo re-export.
- **`strip_bone_display_meshes` só corre com armature na cena**: sem esse guard, um mesh legítimo chamado `Icosphere` (prop/teste) era apagado e a cena ficava vazia. O decode KTX2/meshopt vive no `bpy_mesh.import_gltf` — wrappers como `aigamekit_lab.glb_import` não devem decodificar outra vez.

- **Validação GLB — `aigamekit-lab check glb`**: usa `AiGameKitLab/src/aigamekit_lab/glb_meta.py` (parser binário do GLB sem `bpy`) para extrair `attributes_present`, `extensions_used`, `texture_mime_types`, `v_per_tri`, `world_bounds_y_min`. Aceita `--category <lod0|lod1|lod2|rigged|collision>` (regras YAML em `GameAssets/src/gameassets/data/rules/*.yaml`) e `--no-bpy-inspect` para correr sem Blender. Regras suportam `mesh_totals.v_per_tri`, `attributes_required`, `texture_format`, `compression`, `origin.y_min`, `face_count.max_per_category`.
- **Debug visual GLB — `aigamekit-lab debug viz <glb> -m <modo>`**: 6 modos de visualização de mesh (`normals`, `normals-arrows`, `orientation`, `uv`, `edges`, `weights` com sub-vistas dominant/count/unweighted/`--bone`), `--wireframe` transversal, legendas Pillow embutidas e `viz_report.json` (`AiGameKitLab/src/aigamekit_lab/viz.py`). Imports de deliverables decodificam KTX2/meshopt automaticamente via `aigamekit_shared.gltf_decode.bpy_readable_glb` (`@gltf-transform/cli`). Armadilhas bmesh: seam-splits do glTF criam boundary/flipped falsos — weld `remove_doubles` 1e-4 antes das métricas; modifier WIREFRAME precisa de `use_even_offset=False` (senão explode ±32k em slivers); geometria derivada de rigged tem de amostrar o mesh do depsgraph avaliado (pose ≠ rest); `strip_bone_display_meshes` só atua com armature na cena. Detalhes: `docs/findings/MESH_PIPELINE_FINDINGS.md` §Debug visual.

- **Normais no export GLTF (Text3D)**: NÃO usar `normals_split_custom_set(loop_normals)` em `mesh_lod.py`/`mesh_remesh_textured.py` — o exporter GLTF fica com `V/Tri=3` (normais por loop, sem merge) e infla ficheiros (ex. goblin_shape 33 MB). Usar `shade_smooth` + `auto_smooth_angle` para obter normais por vértice. O passe `weld_glb` pós-export foi removido (era no-op desde que o fecho passou a ser do «voxel merge»/morph-close do topology-fix).
- **Caminho quente do shape — contar/ler mesh sem iterar bpy em Python**: `tri_count(mesh)` (`len(loops) - 2*len(polygons)`, O(1)) e `vertex_coords` / `foreach_get` substituíram list comprehensions em `morphological_close`, `mesh_to_trimesh` e `text3d.utils.mesh_base_plane`. Medido em 1.4M polys: contagem de triângulos 262 ms → ~0 ms, normais de face 980 → 202 ms, centros 917 → 169 ms, vértices 395 → 70 ms (resultados idênticos até ao float32 do bpy). Iterar `polygons`/`vertices` só para agregar é regressão de performance.
- **«Voxel merge» (morph-close) é o dono do fecho de rachas**: `DEFAULT_MORPH_VOXELS` = `0.18` (terrain/rock 3× = `0.54`), N × voxel_m do MC. Como o fecho volumétrico já sela rachas e double-shell, as stages a jusante não devem acrescentar welds/passes de limpeza «por garantia» — no caminho comum (`grid_clamped`, voxel > distance/2) corre um único voxel remesh em vez da cadeia dilate/erode.


- Multi-GPU: a maioria dos pacotes com GPU agora aceitam `--gpu-ids 0,1` para dividir pesos entre GPUs via accelerate (`MultiGPUPlanner` em `aigamekit_shared.multi_gpu`). GameAssets batch/`resume` propaga `--gpu-ids` e `CUDA_VISIBLE_DEVICES` a todos os sub-tools; deteta GPUs via NVML (`aigamekit_shared.gpu.detect_gpu_ids`) quando omitido. Pipeline stages (3D, rig, animate) são agora auto-detetados do manifest + `game.yaml` blocks; usar `--no-3d`, `--no-rig`, `--no-animate` para opt-out. O env var `PAINT3D_MULTI_GPU` está obsoleto — usar `--gpu-ids`. Resolução por defeito do Text2D passou de 2048 para 1024.

- **vramd respawn:** editar tool → `vramd respawn <backend>` (não restart do supervisor). Supervisor só para código ModelServer / `backends.yaml` / protocolo partilhado. Ver Model Server acima + [`docs/findings/UMS_VRAM_FINDINGS.md`](docs/findings/UMS_VRAM_FINDINGS.md).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
