# GameDev

**Docs:** English · [Português (`README_PT.md`)](README_PT.md)

[![CI](https://github.com/maikramer/GameDev/actions/workflows/ci.yml/badge.svg)](https://github.com/maikramer/GameDev/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](Text2D/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Monorepo for **text-to-image**, **text-to-3D**, **text-to-audio**, **textures and skymaps** (Hugging Face Inference API), **PBR texturing**, **part decomposition**, **rigging**, **animation**, and **asset batching**, sharing the same foundation (`gamedev-shared`), unified installer, and documentation.

## Projects

| Folder | Description |
|--------|-------------|
| [**Shared**](Shared/) | Shared library (`gamedev-shared`): logging, GPU, subprocess, installers, CLI. |
| [**Text2D**](Text2D/) | **Text-to-image** CLI with FLUX (SDNQ quantization), aimed at modest GPUs. |
| [**Text3D**](Text3D/) | **Text-to-3D** pipeline: 2D image (via Text2D) → GLB mesh with Hunyuan3D-2.1 (SDNQ INT4). Texturing via Paint3D (optional). |
| [**Part3D**](Part3D/) | **Semantic 3D parts**: Hunyuan3D-Part (segmentation / mesh parts). |
| [**Paint3D**](Paint3D/) | **3D texturing**: Hunyuan3D-Paint 2.1 (multiview PBR) + Materialize PBR + AI upscale (Real-ESRGAN). Standalone or via Text3D. |
| [**GameAssets**](GameAssets/) | **Prompt/asset batching**: profile + CSV → `text2d` or `texture2d` + optional `text3d`, rig, **Animator3D** (`--with-animate`), **`gameassets dream`** (idea → Vite scaffold). |
| [**Texture2D**](Texture2D/) | **Seamless 2D textures** (tileable) via HF Inference API — no local GPU required. |
| [**Skymap2D**](Skymap2D/) | **Equirectangular 360° skymaps** via HF Inference API — skyboxes for game dev, no local GPU. |
| [**Text2Sound**](Text2Sound/) | **Text-to-audio** CLI with Stable Audio Open 1.0: stereo 44.1 kHz, game-dev presets. |
| [**Rigging3D**](Rigging3D/) | **rigging3d** — 3D auto-rigging with [**UniRig**](https://github.com/VAST-AI-Research/UniRig) (skeleton + skinning + merge); CUDA GPU; Python **3.11**, **bpy** 5.0.x (Open3D). |
| [**Animator3D**](Animator3D/) | **animator3d** — **bpy** 5.1; Python **3.13**; procedural clips, **`game-pack`** (humanoid/creature/flying presets), GLB export after rigging. |
| [**Materialize**](Materialize/) | **PBR maps** CLI (Rust/wgpu): normal, AO, metallic, smoothness from a diffuse texture. |
| [**GameDevLab**](GameDevLab/) | **Lab CLI**: debug 3D, quantization benches, profiling, pipeline optimization. |
| [**VibeGame**](VibeGame/) | **vibegame** — TypeScript 3D engine (ECS, Three.js, declarative XML); **Bun** + **Vite**. See [VibeGame/README.md](VibeGame/README.md). |

Each project has its own `README`, setup, requirements, and license. Portuguese: [`README_PT.md`](README_PT.md) (root) and per-package `README_PT.md` where provided.

## Architecture

```
GameDev/
  Shared/           ← gamedev-shared (pip): logging, GPU, subprocess, env, installers
  Text2D/           ← text2d (pip) — depends on Shared
  Text3D/           ← text3d (pip) — depends on Shared + Text2D; texture via Paint3D (optional)
  Part3D/           ← part3d (pip) — Shared; Hunyuan3D-Part (torch-scatter/cluster)
  Paint3D/           ← paint3d (pip) — depends on Shared; Hunyuan3D-2.1 hy3dpaint + Materialize PBR + upscale
  GameAssets/        ← gameassets (pip) — depends on Shared; calls text2d/texture2d/text3d via subprocess
  Texture2D/         ← texture2d (pip) — depends on Shared; HF inference in the cloud
  Skymap2D/          ← skymap2d (pip) — depends on Shared; equirectangular skymaps via HF
  Text2Sound/        ← text2sound (pip) — depends on Shared; Stable Audio Open 1.0
  Rigging3D/         ← rigging3d (pip) — Shared; inference Py 3.11 + bpy 5.0.x
  Animator3D/        ← animator3d (pip) — Shared; Py 3.13 + bpy 5.1 (animation)
  GameDevLab/        ← gamedev-lab (pip) — depends on Shared; debug 3D, benches, profiling
  Materialize/       ← materialize-cli (cargo) — Python installer uses Shared
  VibeGame/          ← vibegame (npm/Bun + Vite) — browser 3D engine; standalone, not pip
```

## General requirements

- **Python**: most tools require **3.10+**; exceptions: **Rigging3D** (3.11), **Animator3D** (3.13 + `bpy` 5.1). See each folder’s README.
- **VibeGame** uses **Bun** and **Node**-compatible tooling (see `VibeGame/package.json`); run `make test-vibegame` from the repo root after installing Bun.
- **GPU** optional for Text2D; for Text3D/Paint3D/Part3D/Rigging3D, CUDA with enough VRAM is recommended for reasonable runtimes. **Texture2D** and **Skymap2D** do not need a local GPU (Hugging Face API). **GameAssets** only needs a GPU if the profile/row invokes local tools (e.g. text2d, text3d).
- **Model weights** (Hugging Face, etc.) have their own licenses — read the model cards before shipping or using in production.

## Quick start

Full guide (tool table, minimum Python per CLI, **repo root vs `Project/scripts/`**): **[docs/INSTALLING.md](docs/INSTALLING.md)** · [Português](docs/INSTALLING_PT.md).

**Game pipeline (GameAssets → Vite / VibeGame, folder layout, GLB handoff):** [docs/MONOREPO_GAME_PIPELINE.md](docs/MONOREPO_GAME_PIPELINE.md).

**Zero-to-game with AI (generative tools + orchestration + agents):** [docs/ZERO_TO_GAME_AI.md](docs/ZERO_TO_GAME_AI.md) · [Português](docs/ZERO_TO_GAME_AI_PT.md).

### Installation options

| Method | When to use |
|--------|-------------|
| **Root scripts** (`./install.sh`, `.\install.ps1`, `install.bat`) | Recommended: prepares installer deps (e.g. Rich), creates a `.venv` per project, editable install. |
| **`gamedev-install`** | After `pip install -e Shared/` (or `PYTHONPATH` pointing at `Shared/src`): same registry as the scripts; useful in CI or when Shared is already installed. |
| **Project-local installer** (`<Project>/scripts/install.sh` or `python scripts/installer.py`) | Shortcut when you are already inside the project folder; do **not** confuse with `GameDev/install.sh` at the repo root (see [docs/INSTALLING.md](docs/INSTALLING.md)). |
| **Manual / pipelines** | `python -m venv .venv` + `pip install -e .` per folder; see READMEs and “Manual” sections — for debugging or CI without the unified wrapper. |

Useful variable: **`PYTHON_CMD`** (or `--python` on the installer) to force the interpreter (default `python3` on Unix, `python` on Windows in the scripts).

### Unified installer (recommended)

The monorepo includes a unified installer for every registered tool:

```bash
# Linux/macOS
./install.sh --list                     # List available tools
./install.sh materialize                # Install Materialize (Rust)
./install.sh text2d                     # Creates Text2D/.venv if needed; installs into project venv
./install.sh texture2d                  # Same (Texture2D/.venv)
./install.sh skymap2d                   # Skymap2D (equirectangular skymaps; no GPU)
./install.sh text2sound                 # Text2Sound (needs CUDA; installs PyTorch)
./install.sh text3d                     # Text3D (Text2D + Hunyuan; nvdiffrast for Paint)
./install.sh gameassets                 # GameAssets (batch; orchestrates other CLIs)
./install.sh part3d                     # Part3D (Hunyuan3D-Part; torch-scatter/cluster)
./install.sh paint3d                    # Paint3D (texturing + nvdiffrast)
./install.sh rigging3d                  # Rigging3D (bundled UniRig + PyTorch/CUDA via installer)
./install.sh animator3d                 # Animator3D (bpy / animation; no PyTorch)
./install.sh gamedevlab                 # GameDevLab (debug 3D, benches, profiling)
./install.sh all                        # Install everything present

# Windows PowerShell (recommended on Windows: script detects `python` and passes it to the installer)
.\install.ps1 --list
.\install.ps1 materialize
.\install.ps1 text2d
.\install.ps1 texture2d
.\install.ps1 skymap2d
.\install.ps1 text2sound
.\install.ps1 text3d
.\install.ps1 gameassets
.\install.ps1 part3d
.\install.ps1 paint3d
.\install.ps1 rigging3d
.\install.ps1 animator3d
.\install.ps1 gamedevlab
.\install.ps1 all

# Windows CMD (same: `install.bat` passes the interpreter to the installer)
install.bat materialize
```

Equivalent with Shared installed: `gamedev-install text2d`, `gamedev-install all`, etc. (list: `gamedev-install --list`).

Unified installer options:

| Option | Description |
|--------|-------------|
| `--action {install,uninstall,reinstall}` | Action (default: install) |
| `--use-venv` | Legacy (optional); the installer **always** creates `project/.venv` if missing and installs there |
| `--skip-deps` | Skip system dependencies |
| `--skip-models` | Skip model/weight setup |
| `--force` | Force reinstall |
| `--prefix PATH` | Install prefix (default: ~/.local) |
| `--python CMD` | Python command (default: python3) |
| `--list` | List available tools |
| `--skip-env-config` | Text3D: do not write `~/.config/text3d/env.sh` (or `env.bat` on Windows) |

### Manual installation

```bash
# 1. Install Shared (required for all Python projects)
cd Shared && pip install -e . && cd ..

# 2. Text2D (image)
cd Text2D && ./scripts/setup.sh && source .venv/bin/activate && text2d --help

# 3. Text3D (3D; depends on Text2D as a local package — see Text3D/README)
cd ../Text3D
python -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt && pip install -e .
text3d --help

# 4. Part3D (semantic parts; torch-scatter/cluster after PyTorch — see Part3D/README)
cd ../Part3D && python -m venv .venv && source .venv/bin/activate && pip install -e . && part3d --help

# 5. Paint3D (Hunyuan3D-Paint 2.1; vendored code in Paint3D/src/paint3d/hy3dpaint/ + nvdiffrast — see Paint3D/docs/PAINT_SETUP.md)
cd ../Paint3D
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision
pip install -r config/requirements.txt && pip install -e .
pip install git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation
paint3d --help

# 6. GameAssets (batch; Text2D/Text3D on PATH or TEXT2D_BIN/TEXT3D_BIN; Texture2D optional TEXTURE2D_BIN; Materialize optional MATERIALIZE_BIN)
cd ../GameAssets && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && gameassets --help

# 7. Texture2D (seamless textures via HF API; no local PyTorch)
cd ../Texture2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && texture2d --help

# 8. Skymap2D (equirectangular 360° skymaps via HF API; no local PyTorch)
cd ../Skymap2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && skymap2d --help

# 9. Text2Sound (text-to-audio; Stable Audio Open 1.0; needs CUDA)
cd ../Text2Sound && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && text2sound --help

# 10. Rigging3D (CUDA GPU; Python 3.11; heavy deps — prefer ./install.sh rigging3d)
cd ../Rigging3D && pip install -e ".[inference,dev]" && rigging3d --help

# 11. Animator3D (animation; venv with Python 3.13 + bpy — see Animator3D/README; Windows: py -3.13 -m venv .venv)
cd ../Animator3D && python3.13 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && animator3d --help

# 12. Materialize (Rust — needs cargo)
cd ../Materialize && ./install.sh

# 13. GameDevLab (debug 3D, benches, profiling; no PyTorch required)
cd ../GameDevLab && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && gamedev-lab --help
```

Full instructions: [docs/INSTALLING.md](docs/INSTALLING.md), [docs/NEW_TOOLS.md](docs/NEW_TOOLS.md) (registering new tools), [Shared/README.md](Shared/README.md), and each package README.

## Licenses

| Component | License | Note |
|-----------|---------|------|
| Monorepo code (Text2D, Text3D, Part3D, Paint3D, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, GameAssets, GameDevLab, Shared) | MIT | See `LICENSE` in each folder |
| Materialize CLI (Rust) | MIT | [Materialize/LICENSE](Materialize/LICENSE) |
| FLUX.2 Klein 4B (official, BF16) | Apache 2.0 | [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) — commercial use allowed per model card; more VRAM than SDNQ |
| FLUX.2 Klein 4B SDNQ (Text2D default) | FLUX Non-Commercial (HF metadata) | [Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) declares `flux-non-commercial-license`; **not** the same as the official Apache 2.0 checkpoint. For commercial products prefer `TEXT2D_MODEL_ID=black-forest-labs/FLUX.2-klein-4B` or a BFL agreement |
| Hunyuan3D-2.1 (shape + paint, Text3D + Paint3D) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) — read repo `LICENSE`: territory restrictions (e.g. EU, UK, South Korea), acceptable use, downstream obligations. Code: [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1); shape weights `hunyuan3d-dit-v2-1` (SDNQ INT4), paint weights `hunyuan3d-paintpbr-v2-1` |
| Stable Audio Open 1.0 / Open Small (Text2Sound) | Stability AI Community License | [stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0), [stabilityai/stable-audio-open-small](https://huggingface.co/stabilityai/stable-audio-open-small) — **gated** models (accept on Hub); free commercial use with annual revenue cap (see repo `LICENSE.md`, currently ~USD 1M; changes: [stability.ai/license](https://stability.ai/license)) |
| Flux-Seamless-Texture-LoRA (Texture2D) | Apache 2.0 (HF metadata) | [gokaygokay/Flux-Seamless-Texture-LoRA](https://huggingface.co/gokaygokay/Flux-Seamless-Texture-LoRA) — LoRA on FLUX.1-dev: also comply with base model and Inference API terms |
| Flux-LoRA-Equirectangular-v3 (Skymap2D) | FLUX.1 [dev] base (NCL) + HF card | [MultiTrickFox/Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — no SPDX in README; base [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) is BFL non-commercial; Civitai origin on card |
| UniRig (code under `Rigging3D/…/unirig/`) | MIT | [VAST-AI-Research/UniRig](https://github.com/VAST-AI-Research/UniRig) · [THIRD_PARTY.md](Rigging3D/THIRD_PARTY.md) |
| UniRig (HF weights) | MIT (many mirrors list MIT) | [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) — confirm in README/`LICENSE` of the snapshot you use; [example with MIT LICENSE](https://huggingface.co/apozz/UniRig-safetensors) |

> **Note:** weights have their own licenses. **Inference API** (Texture2D, Skymap2D): besides the model, [Hugging Face terms](https://huggingface.co/terms-of-service) and API policies apply. **Do not** redistribute checkpoints without complying with the author’s license and attribution. Shap-E (`openai/shap-e`) in legacy Text3D scripts requires accepting Hub terms.

## Environment variables

The monorepo uses environment variables to locate binaries and configure behavior:

| Variable | Used by | Description |
|----------|---------|-------------|
| `TEXT2D_BIN` | GameAssets | Path to `text2d` (if not on `PATH`) |
| `TEXT3D_BIN` | GameAssets | Path to `text3d` |
| `TEXTURE2D_BIN` | GameAssets | Path to `texture2d` |
| `TEXT2SOUND_BIN` | GameAssets | Path to `text2sound` |
| `MATERIALIZE_BIN` | GameAssets, Text3D | Path to `materialize` |
| `GAMEDEVLAB_BIN` | GameAssets | Path to `gamedev-lab` |
| `TEXT2D_MODEL_ID` | Text2D | HF model override for Text2D |
| `TEXTURE2D_MODEL_ID` | Texture2D | HF model override for Texture2D |
| `SKYMAP2D_MODEL_ID` | Skymap2D | HF model override for Skymap2D |
| `HF_TOKEN` | Text2Sound, Texture2D, Skymap2D | Hugging Face token for authenticated APIs |
| `HF_HOME` | All (Python) | Hugging Face cache directory (default: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Text2D, Text3D, GameAssets | CUDA allocator config (auto-set if empty) |
| `TEXT3D_ALLOW_SHARED_GPU` | Text3D | Allow GPU sharing with other processes |
| `TEXT3D_GPU_KILL_OTHERS` | Text3D | Control termination of competing GPU processes |
| `TEXT3D_EXPORT_ROTATION_X_DEG` | Text3D | X rotation when exporting mesh (degrees) |
| `PAINT3D_ALLOW_SHARED_GPU` | Paint3D | Allow GPU sharing with other processes |
| `PAINT3D_GPU_KILL_OTHERS` | Paint3D | Control termination of competing GPU processes |
| `RIGGING3D_ROOT` | Rigging3D | Inference tree root (default: bundled package) |
| `RIGGING3D_PYTHON` | Rigging3D | Python interpreter for the inference environment |

## Development

### Quality tooling

| Tool | Scope | Config |
|------|-------|--------|
| [**Ruff**](https://docs.astral.sh/ruff/) | Lint + format (Python) | `ruff.toml` (root) |
| [**MyPy**](https://mypy.readthedocs.io/) | Type-checking (Python) | `mypy.ini` (root) |
| [**Pytest**](https://pytest.org/) + **pytest-cov** | Tests + coverage | `pyproject.toml` per package |
| [**Cargo Clippy**](https://doc.rust-lang.org/clippy/) | Lint (Rust) | via Makefile |
| [**Pre-commit**](https://pre-commit.com/) | Pre-commit hooks | `.pre-commit-config.yaml` |
| [**GitHub Actions**](https://github.com/features/actions) | CI (lint + test + clippy) | `.github/workflows/ci.yml` |

### Makefile (GNU Make)

```bash
make help            # List targets
make lint            # Ruff check + Cargo clippy
make fmt             # Ruff format + Cargo fmt
make fmt-check       # Check formatting without writing
make test            # Pytest all packages + Cargo test
make test-shared     # Pytest Shared only
make test-text2d     # Pytest Text2D only
make test-gamedevlab # Pytest GameDevLab only
make typecheck       # MyPy on Shared/src
make check           # lint + fmt-check + typecheck + test (full CI)
make clean           # Remove __pycache__, caches, builds
make install-hooks   # Install pre-commit hooks
```

> **Windows:** requires GNU Make (Git Bash, MSYS2, or WSL).

### Dev setup

```bash
# 1. Pre-commit hooks
pip install pre-commit
make install-hooks

# 2. Dev deps for a package (example: Shared)
cd Shared && pip install -e ".[dev]" && cd ..

# 3. Run tests
make test-shared

# 4. Lint and format
make lint
make fmt
```

### pyproject.toml

Each Python package has a `pyproject.toml` (PEP 621) with metadata, dependencies, and pytest config.
Existing `setup.py` files remain for legacy installer compatibility.

## Contributing

- Prefer small commits and [Conventional Commits](https://www.conventionalcommits.org/)-style messages.
- Virtual environments and caches are ignored: root `.gitignore` aligns with subfolders.
- Run `make check` before opening PRs.
- Each tool has `[project.optional-dependencies] dev` in `pyproject.toml` — use `pip install -e ".[dev]"` before running tests.
- **Documentation:** keep `README.md` (English) and optional `README_PT.md`, and `docs/` when present, up to date.
