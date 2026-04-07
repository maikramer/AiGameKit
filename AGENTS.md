# AGENTS.md — GameDev Monorepo

Guide for agentic coding agents working in this repository.

## Repository Overview

Monorepo for game-dev AI tools: text-to-image, text-to-3D, text-to-audio, textures, skymaps, PBR map generation, part decomposition, rigging, animation, asset batching, and a browser 3D engine. Primarily Python with one Rust crate (Materialize) and one TypeScript package (VibeGame).

**Key directories:**

| Directory | Language | Package name | Description |
|-----------|----------|--------------|-------------|
| `Shared/` | Python | `gamedev-shared` | Shared lib (logging, GPU, subprocess, installers, CLI) |
| `Text2D/` | Python | `text2d` | Text-to-image (FLUX SDNQ) |
| `Text3D/` | Python | `text3d` | Text-to-3D (Hunyuan3D-2.1 SDNQ) |
| `Paint3D/` | Python | `paint3d` | 3D texturing (Hunyuan3D-Paint 2.1, bilateral smooth, bake_exp=6) |
| `Part3D/` | Python | `part3d` | Semantic 3D parts |
| `GameAssets/` | Python | `gameassets` | Batch asset generation |
| `Texture2D/` | Python | `texture2d` | Seamless 2D textures (HF API) |
| `Skymap2D/` | Python | `skymap2d` | 360-degree skymaps (HF API) |
| `Text2Sound/` | Python | `text2sound` | Text-to-audio (Stable Audio Open) |
| `Rigging3D/` | Python | `rigging3d` | Auto-rigging (UniRig, Python 3.11) |
| `Animator3D/` | Python | `animator3d` | Animation (bpy 5.1, Python 3.13); `game-pack` (rigged → animated GLB); clip commands `run`, `jump`, `fall` |
| `GameDevLab/` | Python | `gamedev-lab` | Debug 3D, benches, profiling |
| `Materialize/` | Rust | `materialize-cli` | PBR map generation (wgpu compute) |
| `VibeGame/` | TypeScript | `vibegame` (npm) | 3D game engine (bitecs, Three.js, Vite build; Bun tests); `gltf-anim` plugin; `player-gltf` recipe |

All Python packages depend on `gamedev-shared` (install Shared first). VibeGame is standalone (Bun + Vite); it does not use `gamedev-shared`.

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
make test-text2d       # pytest Text2D only
make test-text3d       # pytest Text3D only
make test-paint3d      # pytest Paint3D only
make test-part3d       # pytest Part3D only
make test-gameassets   # pytest GameAssets only
make test-texture2d    # pytest Texture2D only
make test-text2sound   # pytest Text2Sound only
make test-materialize  # cargo test in Materialize/
make test-vibegame     # bun install (frozen) + bun test in VibeGame/
```

Animator3D CLI (see [`docs/ANIMATOR3D_AFTER_RIG.md`](docs/ANIMATOR3D_AFTER_RIG.md)):

```bash
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
# or: python3 -m gamedev_shared.installer.unified vibegame
```

This runs `bun install --frozen-lockfile` and `bun run build` in `VibeGame/`, then installs `vibegame` into `~/.local/bin` (wrapper → `scripts/vibegame-cli.mjs`). Subcommands: `vibegame create <name>`, `vibegame --version`.

**GLB handoff (Text3D / Paint3D / GameAssets → browser):** `loadGltfToScene`, `loadGltfAnimated`, or `loadGltfToSceneWithAnimator` from `vibegame` (`VibeGame/src/extras/gltf-bridge.ts`); declarative `<gltf-load url="…">` or `<player-gltf model-url="…">` (`VibeGame/src/plugins/gltf-xml/`, player recipe). Clips: `GltfAnimator` (`VibeGame/src/extras/gltf-animator.ts`); ECS plugin `gltf-anim` optional. Equirect sky → PMREM: `applyEquirectSkyEnvironment` (`VibeGame/src/extras/sky-env.ts`). Pack: `gameassets handoff --public-dir …` (prefers animated GLB when present). Layout: [`docs/MONOREPO_GAME_PIPELINE.md`](docs/MONOREPO_GAME_PIPELINE.md). Examples: [`VibeGame/examples/monorepo-game/`](VibeGame/examples/monorepo-game/), [`VibeGame/examples/simple-rpg/`](VibeGame/examples/simple-rpg/). Animator3D: [`docs/ANIMATOR3D_AFTER_RIG.md`](docs/ANIMATOR3D_AFTER_RIG.md). AI: [`docs/ZERO_TO_GAME_AI.md`](docs/ZERO_TO_GAME_AI.md).

Declarative GLB player in world XML:

```html
<player-gltf pos="0 0 0" model-url="/assets/models/hero.glb"></player-gltf>
```

**Idea-to-game (`gameassets dream`):** `gameassets dream "description" --dry-run` calls an LLM to plan assets+scene, emits `game.yaml`/`manifest.csv`/`world.xml`/`main.ts`/`index.html`, runs batch+sky+handoff, and scaffolds a playable Vite project. Use `--with-animate` to include an auto-animation step (Animator3D) in the pipeline when applicable. Source: `GameAssets/src/gameassets/dream/` (planner, emitter, runner, llm_context). Providers: `--llm-provider openai|huggingface|stdin`. `--dry-run` generates files without GPU.

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

- **Target version:** Python 3.10+
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

from gamedev_shared.env import get_tool_bin
from gamedev_shared.logging import Logger
```

- Always use `from __future__ import annotations` as the first import.
- Stdlib imports first, then third-party, then local (enforced by `I`/isort rule).
- Use lazy imports for heavy dependencies (`torch`, `diffusers`) to allow importing without GPU deps.
- Never use wildcard imports.

### Type hints

- Required for `gamedev-shared` package (`disallow_untyped_defs = True` in `mypy.ini`).
- Use modern syntax: `str | None` (not `Optional[str]`), `list[str]` (not `List[str]`).
- Use `from __future__ import annotations` to enable forward-reference syntax on Python 3.10.
- `Any` is acceptable for dynamic/generic objects (e.g., pipeline objects from diffusers).

### Naming conventions

- **Modules:** `snake_case` (e.g., `subprocess_utils.py`, `cli_rich.py`)
- **Packages:** `lowercase` (e.g., `text2d`, `gamedev_shared`)
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

- Framework: **pytest** with `pytest-cov`.
- Test file location: `<Package>/tests/test_<module>.py`.
- Test class organization: group by tested feature in classes (`class TestFeatureName:`).
- Use `capsys` fixture for stdout/stderr assertions.
- Use `unittest.mock.patch` and `patch.dict(os.environ, ...)` for env isolation.
- GPU-dependent tests should auto-skip without CUDA (use `pytest.importorskip` or guards).
- Config in each `pyproject.toml`: `pythonpath = ["src"]`, `testpaths = ["tests"]`.

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
    installer.py          # Package installer (uses gamedev-shared)
```

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

1. **lint:** ruff check + ruff format --check + pre-commit
2. **test-python:** pytest per package on multiple Python versions (3.10, 3.12, 3.11 for Rigging3D, 3.13 for Animator3D)
3. **test-rust:** cargo fmt --check + cargo clippy + cargo test (Materialize; continue-on-error)

Excluded from CI (heavy PyTorch / diffusers deps, not viable on GPU-less runners): Text2D, Text3D, Paint3D.
VibeGame has its own CI workflow in `VibeGame/.github/workflows/` (Bun + TypeScript).

## Important Notes

- Pre-commit (ruff + mypy) does **not** run VibeGame ESLint/Prettier; use `make lint-vibegame` / `make fmt-check-vibegame` locally or rely on the **vibegame** CI job.
- Do NOT modify vendored code in `Paint3D/src/paint3d/hy3dpaint/`, `Paint3D/src/paint3d/hunyuan3d-2.1/`, or `Rigging3D/src/rigging3d/unirig/` — these are excluded from lint.
- Shared must be installed before any other package: `cd Shared && pip install -e .`
- Each package may have its own `.venv/` — tests should use the package-local venv.
- Environment variables are the primary configuration mechanism (see README.md "Environment variables" section).
- Run `make check` before considering work complete.

## Learned User Preferences

- Prefere explicações e pedidos de funcionalidade em português ao trabalhar neste repositório.
- Para o fluxo `vibegame run`, quer um loop rápido de desenvolvimento da engine: instalação de dependências no app deve ser opcional, não obrigatória em todo uso.
- Spawner e conteúdo declarativo no VibeGame: manter o mesmo estilo de recipes/parsers/XML em `index.html` já usado no projeto.
- Spawner: diferenciar objetos estáticos (árvores, props) de dinâmicos (caixas empurráveis, inimigos em movimento, etc.) e usar perfis que definam defaults automáticos por tipo de objeto.
- Ajustes de spawn e terreno: priorizar soluções que não degradem muito a performance do mapa.

## Learned Workspace Facts

- Terreno em exemplos VibeGame pode parecer voxel/escadinha; amostragem de altura/normal com um único ponto tende a falhar — estratégias multi-amostra ou suavização costumam ser necessárias para alinhar props ao chão.
- Problemas de árvores a flutuar ou enterrar foram atribuídos em grande parte a pivô/origem do GLB no centro do mesh em vez da base; o utilizador espera que a pipeline Text3D/GameAssets posicione a origem na base por omissão, com pivô ao centro só quando explicitamente adequado ao tipo de asset.
- O comando `vibegame run` foi concebido para rebuild/atualização da engine face a exemplos que usam `file:vibegame`; em Windows podem ocorrer falhas de cópia/cache (`ENOENT` no pacote `vibegame`) e é preciso alvo/cwd coerente com a raiz da engine ou exemplo com `dev` ligado à engine.
- O modelo HF Flux-LoRA-Equirectangular-v3 devolve imagens em resolução errada (1024×768 em vez do pedido 2048×1024) e com os polos ao centro vertical em vez das bordas; Skymap2D `generator.py` faz auto-resize e shift vertical de 50% para corrigir.
- O `PMREMGenerator` do Three.js ignora `texture.offset`/`repeat` no shader interno — para ajustar UV de texturas equirect antes de `fromEquirectangular()` é necessário manipular o bitmap a nível de píxeis (canvas).
- Convenção equirect Three.js: `u = atan(dir.z, dir.x)`, `v = asin(dir.y)` — centro da imagem = horizonte, topo = zénite, fundo = nadir.
- Corpos dinâmicos GLTF no VibeGame podem ter colisor desalinhado do mesh visível se o centro do AABB não coincidir com a origem da entidade; é necessário definir `Collider.posOffset*` a partir do delta AABB→Transform em espaço local.
