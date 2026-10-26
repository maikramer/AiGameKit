# Text2D — AI Text-to-Image Generation

> Fast, local text-to-image generation using [FLUX.3 Image Klein](https://huggingface.co/black-forest-labs/flux-3-image-klein) with SDNQ quantization. Designed for modest GPUs (6 GB VRAM; hw-auto engata **group offload + CUDA streams + int4** automaticamente — pico ≈ ativação, com folga real). Reference images (up to 10), layout boxes and silhouette-guided pose variations make it useful for **2D animation/character consistency**.

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

## Overview

Text2D is a CLI tool that generates images from text prompts using the FLUX.3 Image Klein model in SDNQ (4-bit dynamic quantization). It integrates with the AiGameKit monorepo pipeline and supports quality presets, multi-GPU inference, and batch generation.

**Default model:** [black-forest-labs/flux-3-image-klein](https://huggingface.co/black-forest-labs/flux-3-image-klein) — 5.6B guidance-distilled transformer + Qwen3-VL-4B text encoder + Flux2 VAE, served through the diffusers **ModularPipeline**. The repo is **gated** (FLUX Kommunity License v1.0 — accept it on the Hub and set `HF_TOKEN`). SDNQ quantization is applied at **runtime** (no pre-quantized checkpoint by default). 16 steps (trained 4-16), no classifier-free guidance.

## Requirements

| Item   | Minimum  | Notes |
|--------|----------|-------|
| Python | 3.13+    | Pinned `>=3.13,<3.14` (`pyproject.toml`) |
| GPU    | Optional | NVIDIA + CUDA recommended for reasonable inference |
| VRAM   | ~6 GB+   | With hw-auto memory-efficient mode (on by default) and 512² resolution; multi-GPU via `--gpu-ids` |
| Disk   | ~25 GB   | HF cache (BF16 base ≈ 20 GB) + SDNQ runtime |

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
text2d generate "sunset landscape" -W 768 -H 768 -s 12 -o sunset.png

# Reproducible output with seed
text2d generate "portrait" --seed 42 -o portrait.png

# Edit / composition with reference images (up to 10; output size = 1st ref unless -W/-H)
text2d generate "the same character running, flat colors" --ref hero.png -o hero_run.png

# Layout boxes (placement/scale, not a hard mask); JSON inline or a .json file
text2d generate "hot-air balloons over a valley" \
  --boxes '[{"id":"b1","desc":"a red balloon","bbox":[0.1,0.1,0.5,0.4]}]' -o balloons.png

# Silhouette-guided consistency (2D animation): extract then use as reference
text2d silhouette hero.png -o hero_sil.png
text2d generate "the same character jumping" --ref-silhouette hero.png -o hero_jump.png

# Multiple images per prompt (hero.png, hero_1.png, hero_2.png)
text2d generate "character portrait" -n 3 -o hero.png

# Multi-GPU: split model across GPUs 0 and 1
text2d generate "epic scene" --gpu-ids 0,1

# Quality preset (overrides resolution and steps)
text2d generate "character design" --quality high
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto | Output file path (`.png` or `.jpg`) |
| `-W, --width` | int | auto (1024 / 1st ref) | Image width in pixels (multiple of 16 recommended) |
| `-H, --height` | int | auto (1024 / 1st ref) | Image height in pixels |
| `-s, --steps` | int | 16 | Inference steps (model trained for 4-16; outside range degrades) |
| `-g, --guidance` | float | — | **Ignored**: FLUX.3 Klein is guidance-distilled (no CFG); kept for compatibility |
| `--ref` | path (repeatable) | — | Reference image for edit/composition (max 10); cite as "the first image", "the second image"... |
| `--boxes` | JSON / path | — | Layout boxes `{id, desc, bbox:[y0,x0,y1,x1]}` in 0-1 (+ `from`/`src_bbox`/`tgt_bbox` for edits) |
| `--ref-silhouette` | path | — | Extracts the silhouette (rembg) and uses it as `--ref` (shape consistency) |
| `-n, --num-images` | int | 1 | Images per prompt (suffixes `_1`, `_2`, ...) |
| `--upsample` | flag | off | Prompt upsampler 9B (`flux-3-image-klein-pup`) — expands the short prompt into a full scene caption. Much better direction/pose adherence (e.g. side views) at ~+18 GB weights and **~5 min/image on a 6 GB GPU**; the expanded caption is exposed in `metadata["caption"]` |
| `--seed` | int | — | Reproducible generation seed |
| `--cpu` | flag | off | Force CPU inference |
| `-m, --model` | str | auto | Model ID override (see `text2d models`) |
| `--profile` | flag | off | Measure timing, CPU, RAM, and VRAM |
| `--gpu-ids` | str | auto | GPU IDs for multi-GPU split (e.g. `0,1`) |
| `--quality` | str | `medium` | Quality tier: `fast` / `low` / `medium` / `high` / `highest` |
| `--hw-auto/--no-hw-auto` | flag | on | Hardware auto-detection: picks the SDNQ preset and placement (GO+streams / full-GPU / multi-GPU split). One model since FLUX.3 — no 4B/9B ladder. Explicit flags win. Env kill-switch: `TEXT2D_HW_AUTO=0` |
| `--group-offload/--no-group-offload` | flag | on | **Group offload + CUDA streams** whenever full-GPU wouldn't have headroom (peak ≈ activation; smaller chunks via VAE tiling + attention slicing). Quant stays int4 (int3/int2 only as last resort on tiny GPUs). Kill-switch: `TEXT2D_GROUP_OFFLOAD=0` / `AIGAMEKIT_GROUP_OFFLOAD=0` |
| `--compile/--no-compile` | flag | off (`generate`); **on** (`generate-batch`) | `torch.compile` on the transformer (~−6–10% hot; cold warmup costly) |
| `--compile-mode` | str | `default` | `default` / `reduce-overhead` / `max-autotune` (Inductor; reduce-overhead only with full-GPU) |
| `--channels-last/--no-channels-last` | flag | off (`generate`); **on** (`generate-batch`) | NHWC memory format (Ampere+); pairs well with compile |
| `-v, --verbose` | flag | off | Detailed log output |

When `--quality` is set and explicit `--width` / `--height` / `--steps` are **not** provided, the QualityEngine fills in the tier defaults (see [Quality Presets](#quality-presets)).

Kernel opts on ~6 GB: prefer compile+channels-last for **batch/vramd** (defaults on); keep one-shot `generate` opt-in. Details: [`docs/findings/KERNEL_OPTS_FINDINGS.md`](../docs/findings/KERNEL_OPTS_FINDINGS.md).

### Ícones

Icon generation lives in Text2D via the `icon` asset category — same pipeline (FLUX Klein), icon-tuned defaults:

```bash
text2d generate "espada de fantasia" --category icon --transparent -o icon.png
```

- `--category icon` resolves **512×512, 4 steps** (QualityEngine category; the category beats `--quality` tiers) and augments the prompt with app-icon styling (idempotent — no duplicate instructions if the prompt already says "icon"/"logo").
- `--transparent` removes the background (rembg/U2Net) after generation; output must be `.png` (clear error otherwise).
- `--quality` still applies if you want to fine-tune, but the `icon` category wins over tiers — use `-s`/`-W`/`-H` for explicit overrides.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--category` | str | — | Asset category: `icon` (512², 2 steps, icon styling) |
| `--transparent/--no-transparent` | flag | off | Remove background (rembg/U2Net); requires `.png` output |

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
| `black-forest-labs/flux-3-image-klein` | Default (distilled, 16 steps): BF16 base + SDNQ runtime (**gated** — accept the Kommunity license on Hub). Refs up to 10, layout boxes, 4-16 steps |
| `black-forest-labs/flux-3-image-klein-base` | Undistilled base (50 steps, CFG 3.5) for finetunes/LoRA — via `--model` / `TEXT2D_MODEL_ID` |
| `black-forest-labs/flux-3-image-klein-pup` | Prompt upsampler 9B — loaded only with `--upsample` |

> GGUF weights target ComfyUI-GGUF workflows, not this CLI.

### `text2d poses` + `text2d walk-cycle` (animação 2D)

O FLUX.3 não tem pose conditioning: a pose vem de uma **folha de silhuetas**
desenhada (uma por célula) e a identidade de uma imagem da personagem parada.
O Text2D traz um vocabulário de poses e um comando que gera ciclos de caminhada
prontos a usar.

```bash
# Listar as poses (pernas/braços descritos em texto)
text2d poses

# Um frame com uma pose específica (nome da biblioteca ou texto livre)
text2d generate "knight hero" --pose walk_contact_front -o frame.png

# Ciclo de caminhada completo: 4 frames + sprites + atlas + GIF
text2d walk-cycle "16-bit SNES-era pixel art knight hero in a blue tunic" \
  --direction east --frames 4 --out-dir outputs/sprites/knight
```

| Flag (walk-cycle) | Default | Description |
|-------------------|---------|-------------|
| `--direction` | `east` | `south`/`south_east`/`east`/`north_east`/`north`/`north_west`/`west`/`south_west` |
| `--frames` / `-n` | 4 | Frames do ciclo (fases repetidas; >4 → várias folhas com a mesma identidade) |
| `--out-dir` / `-O` | `outputs/sprites` | Folha crua, frames, sprites, atlas, preview e GIF |
| `--ref` | — | Referência do personagem (identidade, **parado**); sem ela é gerada a 512² |
| `--size` / `--steps` / `--seed` | 320 / 12 / 100 | Altura da célula (largura 0.8x), passos e seed |
| `--sprite-size` / `--colors` | 96 / 32 | Lado do sprite e nº de cores da paleta comum (look 16-bit) |
| `--frame-ms` | 160 | Duração de cada frame no GIF |
| `--measure` | on | Mede a variação entre frames (avisa se forem cópias) |

Como funciona:

1. **Identidade** — a personagem **parada** (`idle`) a 512², ou o `--ref`.
2. **Todos os frames numa só geração** (`4 × 256×320` = 1024×320) com duas
   referências: a identidade e a folha de silhuetas (`poses_00.png`; membros
   do lado de trás a cinzento). Dentro da mesma imagem o modelo mantém
   personagem, escala e linha do chão; a silhueta + a pose por extenso de cada
   célula impõem a passada.
3. **Pós-processamento**: rembg por célula → alinhamento (tronco no mesmo x,
   pés na mesma linha) → downscale por área → alpha binário → **uma paleta
   para o ciclo todo** → `atlas.png` (1x, transparente), `sheet.png`
   (preview 4x) e `cycle.gif`.
4. Direções espelhadas (`west`, `north_west`, `south_west`) são geradas a
   partir da direção canónica e espelhadas — a personagem fica idêntica nos
   dois sets.

Medido na RTX 4050 6 GB (via vramd): **~90 s por ciclo de 4 frames**
(identidade ~55 s + folha ~31 s). Diagnóstico e números:
[`docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md`](../docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md#walk-cycle-v2-folha-única-guiada-por-silhuetas).

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

The `--quality` flag sets resolution and steps from a unified profile system ([`QualityEngine`](../Shared/src/aigamekit_shared/quality.py)). Values are **soft defaults** — explicit CLI flags always take precedence. FLUX.3 Klein is guidance-distilled, so tiers carry **no guidance**.

| Tier | Resolution | Steps |
|------|-----------|-------|
| `fast` | 512×512 | 4 |
| `low` | 768×768 | 8 |
| `medium` | 1024×1024 | 12 |
| `high` | 1024×1024 | 16 |
| `highest` | 1536×1536 | 16 |

```bash
text2d generate "concept art" --quality high    # 1024², 16 steps
text2d generate "thumbnail" --quality fast      # 512², 4 steps
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXT2D_BIN` | Override `text2d` binary path |
| `TEXT2D_MODEL_ID` | Alternative HF model ID (e.g. `black-forest-labs/flux-3-image-klein-base`) |
| `HF_HOME` | Hugging Face cache directory (default: `~/.cache/huggingface`) |
| `TEXT2D_MODELS_DIR` | Local models directory (installer writes to `~/.config/text2d/config.env`) |
| `TEXT2D_OUTPUT_DIR` | Default image output directory |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA memory config (auto-set by mode: `max_split_size_mb` + gc only when group offload is OFF — under GO streaming it fragments) |
| `TEXT2D_GROUP_OFFLOAD` | `0` disables group offload (per-tool kill-switch; `AIGAMEKIT_GROUP_OFFLOAD=0` is the global one) |
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
- **Weights:** default = [black-forest-labs/flux-3-image-klein](https://huggingface.co/black-forest-labs/flux-3-image-klein) (**gated**, FLUX Kommunity License v1.0; Qwen3-VL text encoder Apache-2.0) with SDNQ runtime quantization (MIT, [Disty0/sdnq](https://github.com/Disty0/sdnq)). Optional `--upsample` uses [flux-3-image-klein-pup](https://huggingface.co/black-forest-labs/flux-3-image-klein-pup). Full license table: [AiGameKit/README.md — Licenses](../README.md).
