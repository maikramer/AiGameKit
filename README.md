# AiGameKit

**Docs:** English · [Português (`README_PT.md`)](README_PT.md)

[![CI](https://github.com/maikramer/AiGameKit/actions/workflows/ci.yml/badge.svg)](https://github.com/maikramer/AiGameKit/actions)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![Rust](https://img.shields.io/badge/rust-1.75+-orange.svg)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](Text2D/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Monorepo for **text-to-image**, **text-to-3D**, **text-to-audio**, **seamless textures (local GPU)** and **skymaps** (local GPU), **PBR texturing**, **rigging**, **animation**, **asset batching**, and **browser 3D engine**, sharing the same foundation (`aigamekit-shared`), unified installer, and documentation.

All GPU tools support **multi-GPU** (`--gpu-ids 0,1`) and **quality presets** (`--quality fast|low|medium|high|highest`).

## Pipeline

The tools form a modular generation pipeline — use them individually or let **GameAssets** orchestrate the full flow:

```
  Text2D (image) ──→ Text3D (mesh) ──→ Paint3D (texture) ──→ Rigging3D (rig) ──→ Animator3D (animate)
       │                                        │                                  │
       ▼                                        ▼                                  ▼
  Texture2D (seamless)                   Materialize (PBR)                      GameAssets (batch)
       │                                                                               ──→ VibeGame (browser)
  Skymap2D (sky)
  Text2Sound (audio)
  Terrain3D (terrain)
```

### One-command idea-to-game

The flagship workflow — describe your game and let the pipeline generate everything:

```bash
gameassets dream "A dark fantasy RPG with skeletons and treasure chests" --dry-run   # preview plan
gameassets dream "A dark fantasy RPG with skeletons and treasure chests"              # full run
```

What `dream` does: plans assets via an LLM (`--llm-provider openai|huggingface|stdin`), generates `game.yaml` / `manifest.csv` / `world.xml`, runs the full pipeline (batch → rig → animate → sky → terrain), handoffs assets to Vite public dir, and scaffolds a playable project. Stages are auto-detected; use `--no-animate`, `--no-rig`, or `--no-3d` to opt out.

Source: [`GameAssets/src/gameassets/dream/`](GameAssets/src/gameassets/dream/).

### VibeGame integration

Generated assets flow into the VibeGame browser engine via handoff and declarative XML scenes:

```bash
gameassets handoff --public-dir public/    # copies GLBs (prefers animated) + manifest.json
```

Scene description via `world.xml` using VibeGame recipes:

```html
<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>
<GLTFLoader pos="5 0 0" model-url="/assets/models/skeleton.glb"></GLTFLoader>
<Terrain heightmap-url="/assets/heightmap.png" resolution="128"></Terrain>
```

Key APIs: [`gltf-bridge.ts`](VibeGame/src/extras/gltf-bridge.ts) (`loadGltfToScene`, `loadGltfAnimated`), [`gltf-animator.ts`](VibeGame/src/extras/gltf-animator.ts) (`GltfAnimator`), [`sky-env.ts`](VibeGame/src/extras/sky-env.ts) (`applyEquirectSkyEnvironment`). See [`docs/MONOREPO_GAME_PIPELINE.md`](docs/MONOREPO_GAME_PIPELINE.md) and [`VibeGame/README.md`](VibeGame/README.md).

## Projects

| Folder | Description |
|--------|-------------|
| [**Shared**](Shared/) | Shared library (`aigamekit-shared`): logging, GPU, subprocess, installers, CLI. |
| [**Text2D**](Text2D/) | **Text-to-image** CLI with FLUX (SDNQ quantization), aimed at modest GPUs. |
| [**Text3D**](Text3D/) | **Text-to-3D** pipeline: 2D image (via Text2D) → GLB mesh with Hunyuan3D-Omni (SDNQ INT4; bbox/pose/point/voxel controls). Texturing via Paint3D (optional). |
| [**Paint3D**](Paint3D/) | **3D texturing**: Hunyuan3D-Paint 2.1 (multiview PBR) + Materialize PBR + AI upscale (Real-ESRGAN). Standalone or via Text3D. |
| [**Part3D**](Part3D/) | **Semantic part decomposition**: Hunyuan3D-Part (P3-SAM + X-Part). SDNQ + CPU offload for ~6 GB VRAM. |
| [**GameAssets**](GameAssets/) | **Prompt/asset batching**: profile + CSV → `text2d` or `texture2d` + optional `text3d`, rig, **Animator3D** (auto-detected), **`gameassets dream`** (idea → Vite scaffold). |
| [**Texture2D**](Texture2D/) | **Seamless 2D textures** (tileable) via pattern-diffusion (local GPU) + PBR via Materialize. |
| [**Skymap2D**](Skymap2D/) | **Equirectangular 360° skymaps** — FLUX.1-dev + LoRA locally on GPU (CUDA), skyboxes for game dev. |
| [**Text2Sound**](Text2Sound/) | **Text-to-audio** CLI with Stable Audio Open 1.0: stereo 44.1 kHz, game-dev presets. |
| [**Rigging3D**](Rigging3D/) | **rigging3d** — 3D auto-rigging with [**SkinTokens**](https://github.com/VAST-AI-Research/SkinTokens) (unified autoregressive skeleton + skinning, successor to UniRig); CUDA GPU; Python **3.13**, **bpy** 5.2 LTS. |
| [**Animator3D**](Animator3D/) | **animator3d** — **bpy** 5.2 LTS; Python **3.13**; procedural clips, **`game-pack`** (humanoid/creature/flying presets), GLB export after rigging. |
| [**Materialize**](Materialize/) | **PBR maps** CLI (Rust/wgpu): normal, AO, metallic, smoothness from a diffuse texture. |
| [**AiGameKitLab**](AiGameKitLab/) | **Lab CLI**: debug 3D, quantization benches, profiling, pipeline optimization. |
| [**Terrain3D**](Terrain3D/) | **terrain3d** — AI terrain generation via diffusion models (terrain-diffusion; CUDA GPU). |
| [**VibeGame**](VibeGame/) | **vibegame** — TypeScript 3D engine (ECS, Three.js, declarative XML); **Bun** + **Vite**. See [VibeGame/README.md](VibeGame/README.md). |

Each project has its own `README`, setup, requirements, and license. Portuguese: [`README_PT.md`](README_PT.md) (root) and per-package `README_PT.md` where provided.

### Quality presets & multi-GPU

All generation tools support a unified quality system (`--quality fast|low|medium|high|highest`) with sensible defaults per tool and asset category. See [`docs/superpowers/specs/2026-04-30-quality-presets-design.md`](docs/superpowers/specs/2026-04-30-quality-presets-design.md).

Multi-GPU support (via `accelerate` dispatch) is available across most GPU tools:

```bash
text3d generate "a dragon" --gpu-ids 0,1       # Split weights across GPU 0 and 1
paint3d texture dragon.glb --gpu-ids 0,1       # Multi-GPU texturing
```

Detected automatically via NVML (`aigamekit_shared.gpu.detect_gpu_ids`, dep `nvidia-ml-py`; fallback `nvidia-smi`) when omitted. GameAssets batch/resume propagates `--gpu-ids` to all sub-tools.

## Architecture

```
AiGameKit/
  Shared/           ← aigamekit-shared (pip): logging, GPU, subprocess, env, installers
  Text2D/           ← text2d (pip) — depends on Shared
  Text3D/           ← text3d (pip) — depends on Shared + Text2D; texture via Paint3D (optional)
  Paint3D/           ← paint3d (pip) — depends on Shared; Hunyuan3D-2.1 hy3dpaint + Materialize PBR + upscale
  Part3D/            ← part3d (pip) — depends on Shared; Hunyuan3D-Part (P3-SAM + X-Part)
  GameAssets/        ← gameassets (pip) — depends on Shared; calls text2d/texture2d/text3d via subprocess
  Texture2D/         ← texture2d (pip) — depende de Shared; pattern-diffusion local + PBR via Materialize
  Skymap2D/          ← skymap2d (pip) — depends on Shared; equirectangular skymaps (local FLUX.1-dev + LoRA)
  Text2Sound/        ← text2sound (pip) — depends on Shared; Stable Audio Open 1.0
  Rigging3D/         ← rigging3d (pip) — Shared; SkinTokens Py 3.13 + bpy 5.2 LTS
  Animator3D/        ← animator3d (pip) — Shared; Py 3.13 + bpy 5.2 LTS (animation)
  AiGameKitLab/        ← aigamekit-lab (pip) — depends on Shared; debug 3D, benches, profiling
  Terrain3D/        ← terrain3d (pip) — depends on Shared; AI terrain generation via diffusion
  Materialize/       ← materialize-cli (cargo) — Python installer uses Shared
  VibeGame/          ← vibegame (npm/Bun + Vite) — browser 3D engine; standalone, not pip
```

## General requirements

- **Python**: all tools require **3.13** (each `pyproject.toml` pins `>=3.13,<3.14`); `bpy>=5.2.0` (LTS) for mesh tools. See each folder's README.
- **VibeGame** uses **Bun** and **Node**-compatible tooling (see `VibeGame/package.json`); run `make test-vibegame` from the repo root after installing Bun.
- **GPU** optional for Text2D; for Text3D/Paint3D/Rigging3D, CUDA with enough VRAM is recommended for reasonable runtimes. **Texture2D** runs locally on a CUDA GPU (pattern-diffusion). **Skymap2D** runs locally on a CUDA GPU (FLUX.1-dev + LoRA). **GameAssets** only needs a GPU if the profile/row invokes local tools (e.g. text2d, text3d). **Multi-GPU:** most GPU tools accept `--gpu-ids 0,1` to split model weights across multiple NVIDIA GPUs via accelerate dispatch.
- **Model weights** (Hugging Face, etc.) have their own licenses — read the model cards before shipping or using in production.

## Quick start

Full guide (tool table, minimum Python per CLI, **repo root vs `Project/scripts/`**): **[docs/INSTALLING.md](docs/INSTALLING.md)** · [Português](docs/INSTALLING_PT.md).

**Game pipeline (GameAssets → Vite / VibeGame, folder layout, GLB handoff):** [docs/MONOREPO_GAME_PIPELINE.md](docs/MONOREPO_GAME_PIPELINE.md).

**Hunyuan shape / repair / Part3D lessons** (faces vs X-Part, elephant feet, welded thins): [docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md](docs/HUNYUAN_MESH_AND_PARTS_LESSONS.md) · [Português](docs/HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

**Model findings hub** (VRAM, SDNQ, kernels, Omni, vramd, paint/sky/mesh): [docs/MODEL_FINDINGS.md](docs/MODEL_FINDINGS.md) · [docs/findings/](docs/findings/) · Omni [docs/OMNI_SHAPE_FINDINGS.md](docs/OMNI_SHAPE_FINDINGS.md) · benches [docs/KERNEL_OPTS_BENCH.md](docs/KERNEL_OPTS_BENCH.md).

**GLB compression (KTX2 + meshopt, `text3d finish`):** [docs/GLB_FINISH_COMPRESSION.md](docs/GLB_FINISH_COMPRESSION.md).

**vramd batch waves** (GameAssets shape/paint + optional GPU tools): [docs/GAMEASSETS_UMS_BATCH.md](docs/GAMEASSETS_UMS_BATCH.md).

**Mission / premises** (ease, automate, agent-first, VRAM-as-infra): [docs/mission/](docs/mission/README.md) · summary in [AGENTS.md](AGENTS.md).

**File logging** (all Python tools + vramd → `~/.cache/aigamekit/logs/`): [docs/LOGGING.md](docs/LOGGING.md) · [Português](docs/LOGGING_PT.md).

**Testing** (coverage floor ≥100/tool, suite naming, CPU-first rules): [docs/TESTING.md](docs/TESTING.md) · [Português](docs/TESTING_PT.md).

**Zero-to-game with AI (generative tools + orchestration + agents):** [docs/ZERO_TO_GAME_AI.md](docs/ZERO_TO_GAME_AI.md) · [Português](docs/ZERO_TO_GAME_AI_PT.md).

### Installation options

| Method | When to use |
|--------|-------------|
| **One-liner (Clified, no clone)** | Fastest on a clean machine — installs the Clified engine + a AiGameKit tool from the [remote catalog](https://github.com/maikramer/clified-catalog). |
| **Root scripts** (`./install.sh`, `.\install.ps1`, `install.bat`) | From a clone: [Clified](https://pypi.org/project/clified/) via PyPI using `tools.yaml` in this repo. |
| **`aigamekit-install`** | Same flow via `aigamekit_shared.installer` bridge (installs `clified` via PyPI if needed). |
| **Project-local installer** (`python scripts/installer.py` in a tool folder) | Shortcut when already inside a project folder — **not** the root `AiGameKit/install.sh` (see [docs/INSTALLING.md](docs/INSTALLING.md)). |
| **Manual / pipelines** | `python -m venv .venv` + `pip install -e .` per folder — debugging or CI without the unified wrapper. |

Useful variable: **`PYTHON_CMD`** (or `--python` on the installer) to force the interpreter.

### One-liner (Clified / no clone)

Install the Clified engine and a AiGameKit tool in one step (`~/.local/bin` wrappers; repo public on GitHub):

**Linux / macOS:**

```bash
# Examples — replace <tool> with text2d, text3d, materialize, gameassets, vibegame, all, …
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get text2d
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --get materialize
curl -fsSL https://raw.githubusercontent.com/maikramer/clified/main/install.sh | bash -s -- --catalog   # list all
```

**Windows (PowerShell):**

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get text2d
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/maikramer/clified/main/install.ps1))) --get materialize
```

Catalog keys match `tools.yaml` entries: `text2d`, `text3d`, `texture2d`, `skymap2d`, `text2sound`, `terrain3d`, `rocks3d`, `gameassets`, `aigamekitlab`, `paint3d`, `part3d`, `rigging3d`, `animator3d`, `materialize`, `vibegame`, or `all` for every tool in the checkout.

### Installer via Clified (from clone)

Installation is driven by [`tools.yaml`](tools.yaml) and [Clified](https://pypi.org/project/clified/) on PyPI (installed automatically by the root scripts):

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
./install.sh paint3d                    # Paint3D (texturing + nvdiffrast)
./install.sh rigging3d                  # Rigging3D (SkinTokens + PyTorch/CUDA via installer)
./install.sh animator3d                 # Animator3D (bpy / animation; no PyTorch)
./install.sh aigamekitlab                 # AiGameKitLab (debug 3D, benches, profiling)
./install.sh terrain3d                  # Terrain3D (AI terrain; CUDA GPU)
./install.sh rocks3d                    # Rocks3D (procedural rocks)
./install.sh vibegame                   # VibeGame (Bun + Vite 3D engine)
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
.\install.ps1 paint3d
.\install.ps1 rigging3d
.\install.ps1 animator3d
.\install.ps1 aigamekitlab
.\install.ps1 terrain3d
.\install.ps1 rocks3d
.\install.ps1 vibegame
.\install.ps1 all

# Windows CMD (same: `install.bat` passes the interpreter to the installer)
install.bat materialize
```

Equivalent with Shared installed: `aigamekit-install text2d`, `aigamekit-install all`, etc. (list: `aigamekit-install --list`).

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

# 4. Paint3D (Hunyuan3D-Paint 2.1; vendored code in Paint3D/src/paint3d/hy3dpaint/ + nvdiffrast — see Paint3D/docs/PAINT_SETUP.md)
cd ../Paint3D
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision
pip install -r config/requirements.txt && pip install -e .
pip install git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation
paint3d --help

# 5. GameAssets (batch; Text2D/Text3D on PATH or TEXT2D_BIN/TEXT3D_BIN; Texture2D optional TEXTURE2D_BIN; Materialize optional MATERIALIZE_BIN)
cd ../GameAssets && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && gameassets --help

# 6. Texture2D (seamless textures via pattern-diffusion; local GPU + PBR via Materialize)
cd ../Texture2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && texture2d --help

# 7. Skymap2D (equirectangular 360° skymaps; local FLUX.1-dev + LoRA)
cd ../Skymap2D && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && skymap2d --help

# 8. Text2Sound (text-to-audio; Stable Audio Open 1.0; needs CUDA)
cd ../Text2Sound && chmod +x scripts/setup.sh && ./scripts/setup.sh && source .venv/bin/activate && text2sound --help

# 9. Rigging3D (CUDA GPU; Python 3.13; SkinTokens — prefer ./install.sh rigging3d)
cd ../Rigging3D && pip install -e ".[inference,dev]" && rigging3d --help

# 10. Animator3D (animation; venv with Python 3.13 + bpy — see Animator3D/README; Windows: py -3.13 -m venv .venv)
cd ../Animator3D && python3.13 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && animator3d --help

# 11. Materialize (Rust — needs cargo)
cd ../Materialize && ./install.sh

# 12. AiGameKitLab (debug 3D, benches, profiling; no PyTorch required)
cd ../AiGameKitLab && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && aigamekit-lab --help

# 13. Terrain3D (AI terrain; CUDA GPU)
cd ../Terrain3D && python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]" && terrain3d --help
```

Full instructions: [docs/INSTALLING.md](docs/INSTALLING.md) (incl. registering new tools via `tools.yaml`), [Shared/README.md](Shared/README.md), and each package README.

## Licenses

| Component | License | Note |
|-----------|---------|------|
| Monorepo code (Text2D, Text3D, Paint3D, Texture2D, Skymap2D, Text2Sound, Rigging3D, Animator3D, GameAssets, AiGameKitLab, Terrain3D, Shared) | MIT | See `LICENSE` in each folder |
| Materialize CLI (Rust) | MIT | [Materialize/LICENSE](Materialize/LICENSE) |
| FLUX.2 Klein 4B (official, BF16) | Apache 2.0 | [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) — commercial use allowed per model card; more VRAM than SDNQ |
| FLUX.2 Klein (Text2D default: fp16 base + SDNQ runtime) | 4B: Apache 2.0 · 9B: **gated** (accept terms on Hub) | Text2D loads the official [4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) / [9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) base and applies SDNQ quantization **at runtime** — no pre-quantized checkpoint by default. Pre-quantized [Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) mirrors are optional via `TEXT2D_MODEL_ID` (they declare `flux-non-commercial-license`) |
| Hunyuan3D-Omni (Text3D shape) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) — read repo `LICENSE`: territory restrictions (e.g. EU, UK, South Korea), acceptable use, downstream obligations. SDNQ INT4 on small GPUs |
| Hunyuan3D-2.1 (Paint3D paint) | Tencent Hunyuan Community License | [tencent/Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) — paint weights `hunyuan3d-paintpbr-v2-1`; same territory/use restrictions. Code: [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) |
| Stable Audio Open 1.0 / Open Small (Text2Sound) | Stability AI Community License | [stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0), [stabilityai/stable-audio-open-small](https://huggingface.co/stabilityai/stable-audio-open-small) — **gated** models (accept on Hub); free commercial use with annual revenue cap (see repo `LICENSE.md`, currently ~USD 1M; changes: [stability.ai/license](https://stability.ai/license)) |
| Stable Diffusion 1.5 (Texture2D default) + pattern-diffusion (optional) | SD1.5: CreativeML Open RAIL-M · pattern-diffusion: Apache 2.0 | Default is [stable-diffusion-v1-5/stable-diffusion-v1-5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) (circular padding, no LoRA); [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) (SD2-base fine-tune on 6.8M tileable patterns) via `TEXTURE2D_MODEL_ID` |
| Flux-LoRA-Equirectangular-v3 (Skymap2D) | FLUX.1 [dev] base (NCL) + HF card | [MultiTrickFox/Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — no SPDX in README; base [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) is BFL non-commercial; Civitai origin on card |
| SkinTokens (code under `Rigging3D/…/skintokens/`) | MIT | [VAST-AI-Research/SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) — successor to UniRig · [THIRD_PARTY.md](Rigging3D/THIRD_PARTY.md) |
| SkinTokens (HF weights) | MIT | [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) — auto-downloaded on first run (~1.6 GB) |

> **Note:** weights have their own licenses. **Do not** redistribute checkpoints without complying with the author's license and attribution. Shap-E (`openai/shap-e`) in legacy Text3D scripts requires accepting Hub terms.

## Environment variables

The monorepo uses environment variables to locate binaries and configure behavior:

| Variable | Used by | Description |
|----------|---------|-------------|
| `TEXT2D_BIN` | GameAssets | Path to `text2d` (if not on `PATH`) |
| `TEXT3D_BIN` | GameAssets | Path to `text3d` |
| `TEXTURE2D_BIN` | GameAssets | Path to `texture2d` |
| `TEXT2SOUND_BIN` | GameAssets | Path to `text2sound` |
| `MATERIALIZE_BIN` | GameAssets, Text3D | Path to `materialize` |
| `AIGAMEKITLAB_BIN` | GameAssets | Path to `aigamekit-lab` |
| `TERRAIN3D_BIN` | GameAssets | Path to `terrain3d` |
| `TEXT2D_MODEL_ID` | Text2D | HF model override for Text2D |
| `TEXTURE2D_MODEL_ID` | Texture2D | HF model override for Texture2D (default `stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `SKYMAP2D_MODEL_ID` | Skymap2D | HF model override for Skymap2D (LoRA; default `MultiTrickFox/Flux-LoRA-Equirectangular-v3`) |
| `SKYMAP2D_BASE_MODEL_ID` | Skymap2D | Base FLUX.1-dev override (default `Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32`; official `black-forest-labs/FLUX.1-dev` is gated) |
| `HF_TOKEN` | Text2Sound, Skymap2D, Texture2D | Hugging Face token for **gated** model downloads (accept terms on Hub first) |
| `HF_HOME` | All (Python) | Hugging Face cache directory (default: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Text2D, Text3D, GameAssets | CUDA allocator config (auto-set if empty) |
| `TEXT3D_ALLOW_SHARED_GPU` | Text3D | Allow GPU sharing with other processes |
| `TEXT3D_GPU_KILL_OTHERS` | Text3D | Control termination of competing GPU processes |
| `TEXT3D_EXPORT_ROTATION_X_DEG` | Text3D | X rotation when exporting mesh (degrees) |
| `PAINT3D_ALLOW_SHARED_GPU` | Paint3D | Allow GPU sharing with other processes |
| `PAINT3D_GPU_KILL_OTHERS` | Paint3D | Control termination of competing GPU processes |
| `PART3D_BIN` | Part3D | Override `part3d` binary path |
| `PART3D_HW_AUTO` | Part3D | `0` disables hardware auto-detection |
| `PART3D_ALLOW_SHARED_GPU` | Part3D | Allow GPU sharing with other processes |
| `PART3D_GPU_KILL_OTHERS` | Part3D | Control termination of competing GPU processes |
| `PAINT3D_MULTI_GPU` | Paint3D | **Deprecated** — use `--gpu-ids 0,1` instead. Legacy env var to split VAE across GPUs |
| `RIGGING3D_ROOT` | Rigging3D | Inference tree root (default: bundled package) |
| `RIGGING3D_PYTHON` | Rigging3D | Python interpreter for the inference environment |
| `VRAMD_BIN` | All GPU tools | Path to `vramd` (vramd) |
| `VRAMD_AUTO_START` | All GPU tools | `0` disables auto-start of vramd on first generate |
| `VRAMD_PRIORITY` | All GPU tools / GameAssets | Default queue priority: `interactive` \| `batch` |
| `VRAMD_MAX_AFFINITY_CUTS` | ModelServer | Max VRAM-affinity skips before forcing HOL (default `3`) |
| `VRAMD_MAX_QUEUE_DEPTH` | ModelServer | Job queue depth before `queue_full` (default `32`) |
| `VRAMD_MAX_INFLIGHT` | ModelServer | Parallel generations (default `1`) |
| `AIGAMEKIT_ALLOW_LEGACY_SERVER` | Shared / tools | `1` = opt-in per-tool legacy servers + legacy `ensure_vram` (default off) |
| `AIGAMEKIT_PREFER_MONOREPO` | Shared / GameAssets | Default `1`: `resolve_binary` prefers `<Tool>/.venv/bin` over stale `~/.local/bin` |
| `VRAMD_CLIENT_SOCKET` | Shared | Override Unix socket path (legacy / tests) |
| `AIGAMEKIT_LOG_DIR` | All Python tools + vramd | Directory for daily log files (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | All Python tools + vramd | Exact log file path (overrides per-tool daily naming) |
| `AIGAMEKIT_LOG_TOOL` | All Python tools + vramd | Tool name used in log filename (auto from CLI / `vramd`) |
| `AIGAMEKIT_LOG_LEVEL` | All Python tools + vramd | Min file level: `DEBUG` \| `INFO` \| `WARN` \| `ERROR` (default `INFO`) |
| `AIGAMEKIT_FILE_LOG` | All Python tools + vramd | `0` disables file logging; `1` forces on (needed under pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | All Python tools + vramd | `1` disables file logging |

Logs: `~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log` (vramd → `vramd-….log`). Console stays Rich/ANSI; file is plain text with UTC timestamps. Full guide: [docs/LOGGING.md](docs/LOGGING.md).

## Unified Model Server (vramd)

Every GPU tool (Text2D, Text2Icon, Text3D, Paint3D, Part3D, Texture2D, Skymap2D, Text2Sound, Terrain3D) delegates generation to the **Unified Model Server** — a single supervisor process that owns the machine's VRAM. One socket (`~/.cache/aigamekit/model-server.sock`), one process, global model inventory, no per-tool servers.

**How it works:**

1. Tool CLIs call `delegate_to_vramd` **before** any in-process GPU prep; the vramd auto-starts on first generate (disable with `VRAMD_AUTO_START=0`).
2. Jobs go through `JobQueue` → `AffinityScheduler` → `WorkerPool` (`MAX_INFLIGHT=1` — one generation at a time).
3. Each backend is a **persistent subprocess worker** in the tool's own venv (JSONL stdin/stdout) — after editing tool code, `vramd respawn <backend>` reloads it without restarting the supervisor.
4. Queue priority: `interactive` (CLI) > `batch` (GameAssets sets `VRAMD_PRIORITY=batch`). VRAM-affinity skips cold backends (≤3 cuts), then weight + LRU eviction keeps VRAM inside safe margins.
5. **`hw-auto`** fills peak signals (SDNQ preset, memory-efficient) in the vramd payload — no operator `--low-vram` flag.

```bash
vramd start | stop | status | submit | queue | wait | cancel | flush | backends | preload | evict | reap | respawn | zero | stats | debug | bench | doctor
vramd status                    # backends + HOLDING/QUEUE
vramd queue                     # jobs + timings
vramd wait <job_id>             # block until a job finishes
vramd respawn <backend>         # reload edited tool code in the worker
```

Tool flags: `--vramd-priority interactive|batch`, `--no-vramd`, `--vramd-stream`. WAL: `~/.cache/aigamekit/vramd-jobs.jsonl`. Full guide: [`Vramd/README.md`](Vramd/README.md).

### Models & HF gates

| Tool | Default model(s) | HF gate | Notes |
|------|------------------|---------|-------|
| **Text2D** | [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (low VRAM) / [FLUX.2 Klein 9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) (high VRAM) — fp16 base + **SDNQ runtime** quantization | 9B: **gated** (accept on Hub); 4B: public | `TEXT2D_MODEL_ID` override; hw-auto picks 4B under ~7.5 GB VRAM |
| **Text2Icon** | [Sana 600M 512px](https://huggingface.co/Efficient-Large-Model/Sana_600M_512px_diffusers) (default) / [Clark Air 1.6B 1.58-bit](https://huggingface.co/clark-labs/clark-air-sana-1.6b-1.58bit) (low VRAM) | no | pipeline [Sana 1600M 512px](https://huggingface.co/Efficient-Large-Model/Sana_1600M_512px_diffusers) |
| **Text3D** | [Hunyuan3D-Omni](https://huggingface.co/tencent/Hunyuan3D-Omni) shape (SDNQ INT4; bbox/pose/point/voxel controls) + Text2D FLUX reference image | no | Tencent Community License; BiRefNet bg-removal |
| **Paint3D** | [Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) paint (`hunyuan3d-paintpbr-v2-1`) | no | + Real-ESRGAN (optional upscale) |
| **Part3D** | [Hunyuan3D-Part](https://huggingface.co/tencent/Hunyuan3D-Part) (P3-SAM + X-Part) | no | Tencent Community License |
| **Texture2D** | [Stable Diffusion 1.5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) + circular padding | no | `TEXTURE2D_MODEL_ID` override (e.g. [pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion)) |
| **Skymap2D** | [FLUX.1-dev SDNQ uint4](https://huggingface.co/Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32) base + [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) | no (mirror); official [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) is **gated** | `SKYMAP2D_BASE_MODEL_ID` override |
| **Text2Sound** | [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) (music) / [Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) (effects) | **gated** — accept terms on Hub + `HF_TOKEN` | Stability AI Community License |
| **Rigging3D** | [SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) (TokenRig) | no | successor to UniRig; MIT |
| **Terrain3D** | [terrain-diffusion-30m](https://huggingface.co/xandergos/terrain-diffusion-30m) | no | vendored; WorldClim rasters auto-download |

**Gated models** require accepting terms on the Hugging Face Hub (and `HF_TOKEN` set) before the weights download. Everything runs **locally** — the Hub is only a weight source, never an inference API.

## Development

### Quality tooling

| Tool | Scope | Config |
|------|-------|--------|
| [**Ruff**](https://docs.astral.sh/ruff/) | Lint + format (Python) | `ruff.toml` (root) |
| [**MyPy**](https://mypy.readthedocs.io/) | Type-checking (Python) | `mypy.ini` (root) |
| [**Pytest**](https://pytest.org/) + **pytest-cov** | Tests + coverage | `pyproject.toml` per package |
| [**Cargo Clippy**](https://doc.rust-lang.org/clippy/) | Lint (Rust) | via Makefile |
| [**Pre-commit**](https://pre-commit.com/) | Pre-commit hooks | `.pre-commit-config.yaml` |
| [**GitHub Actions**](https://github.com/features/actions) | CI (lint + mypy + pytest matrix + Materialize + VibeGame Bun) | `.github/workflows/ci.yml` — pitfalls: [`docs/TESTING.md`](docs/TESTING.md) |

### Makefile (GNU Make)

```bash
make help            # List targets
make lint            # Ruff check + Cargo clippy
make fmt             # Ruff format + Cargo fmt
make fmt-check       # Check formatting without writing
make test            # Pytest all packages + Cargo test
make test-shared     # Pytest Shared only
make test-text2d     # Pytest Text2D only
make test-aigamekitlab # Pytest AiGameKitLab only
make test-terrain3d # Pytest Terrain3D only
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

## References

Some components trace their design to external projects:

- **[Materialize](Materialize/)** (Rust CLI): inspired by the original [Materialize](https://github.com/BoundingBoxSoftware/Materialize) from Bounding Box Software (Unity/Windows). See [`Materialize/README.md`](Materialize/README.md).
- **[VibeGame](VibeGame/)** (TypeScript engine): upstream project [dylanebert/VibeGame](https://github.com/dylanebert/vibegame). See [`VibeGame/README.md`](VibeGame/README.md).

## Contributing

- Prefer small commits and [Conventional Commits](https://www.conventionalcommits.org/)-style messages.
- Virtual environments and caches are ignored: root `.gitignore` aligns with subfolders.
- Run `make check` before opening PRs.
- Each tool has `[project.optional-dependencies] dev` in `pyproject.toml` — use `pip install -e ".[dev]"` before running tests.
- **Documentation:** keep `README.md` (English) and optional `README_PT.md`, and `docs/` when present, up to date.
