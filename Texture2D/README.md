# Texture2D — Seamless 2D Texture Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for **seamless (tileable) 2D textures** running locally on GPU with [**pattern-diffusion**](https://huggingface.co/Arrexel/pattern-diffusion) and **PBR maps** via [Materialize](../Materialize/).

Uses the [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) model — a StableDiffusion-2-base fine-tune trained on **6.8M tileable patterns** (Apache-2.0) — to generate textures that repeat without visible seams, ideal for floors, rocks, walls, and game-dev materials. Optional **PBR** (normal / height / metallic / roughness / AO) is produced by invoking the [Materialize](../Materialize/) Rust/wgpu CLI.

In the [GameDev](../README.md) monorepo, the package depends on [**gamedev-shared**](../Shared/) (`gamedev_shared`): quality presets, Rich CLI, GPU helpers, and shared conventions aligned with Text2D, Text3D, and GameAssets.

## Overview

- **Local GPU inference** — pattern-diffusion (SD2-base fine-tune, Apache-2.0), no cloud API needed
- **Seamless by construction** — the model is trained on tileable patterns and inference uses circular `Conv2d` padding (`make_seamless`); the optional `--seamless-method full` noise-rolling recipe yields **zero measurable FID/CLIP loss** on the seam (per the [model card](https://huggingface.co/Arrexel/pattern-diffusion))
- **PBR via Materialize** — when the `materialize` binary is on `PATH` (or `MATERIALIZE_BIN` is set), `texture2d generate` derives normal / height / metallic / roughness / AO maps automatically; otherwise the two-step flow is documented below
- **13 material presets** — Wood, Stone, Grass, Sand, Dirt, Metal, Brick, Fabric, Leather, Concrete, Marble, Gravel, Tile Floor
- **Quality tiers** — `fast`, `low`, `medium` (default), `high`, `highest` via `--quality`
- **Quantization** — `--quant {none,fp8,nf4}` to reduce VRAM (default `none`)
- **Batch generation** — multiple textures from a prompt file
- **Multi-GPU** — `--gpu-ids 0,1` splits weights across GPUs via accelerate
- **JSON metadata** — each texture has a `.json` sidecar with seed, final prompt, and parameters
- **Low VRAM mode** — CPU offloading to fit smaller GPUs

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
./install.sh texture2d
```

The installer creates `Texture2D/.venv`, editable-installs the package, and places a wrapper in `~/.local/bin`. See [docs/INSTALLING.md](../docs/INSTALLING.md) for details.

### Manual / development

```bash
cd Shared && pip install -e .
cd Texture2D && pip install -e .
```

Requires a **CUDA GPU** (PyTorch, diffusers, transformers, accelerate are runtime dependencies).

## Commands

| Command | Description |
|---------|-------------|
| `texture2d generate PROMPT` | Generate a seamless texture |
| `texture2d presets` | List available material presets |
| `texture2d batch FILE` | Batch generate from a prompt file (one per line) |
| `texture2d info` | Config, system, and environment info |
| `texture2d skill install` | Install Cursor Agent Skill |

### `texture2d generate PROMPT`

Generate a seamless tileable texture from a text prompt.

```bash
# Basic usage
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# With a material preset
texture2d generate "weathered surface" --preset Stone -o wall.png

# High quality with a fixed seed
texture2d generate "mossy cobblestone" --quality high --seed 42 -o cobble.png

# Low VRAM mode
texture2d generate "dark marble floor" --low-vram -o marble.png
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto (`outputs/textures/`) | Output file path (`.png`) |
| `-W, --width` | int | 1024 | Image width (multiple of 8) |
| `-H, --height` | int | 1024 | Image height (multiple of 8) |
| `-s, --steps` | int | 28 | Inference steps |
| `-g, --guidance` | float | 3.5 | Guidance scale |
| `--seed` | int | None | Random seed for reproducibility |
| `-n, --negative-prompt` | str | `""` | Negative prompt |
| `-p, --preset` | str | None | Material preset (see Presets below) |
| `--seamless-method` | str | `late` | Seamless strategy: `none` (off), `late` (circular padding only — default), `full` (noise-rolling recipe, strongest guarantee) |
| `--quant` | str | `none` | Model quantization: `none`, `fp8`, `nf4` (lower VRAM) |
| `-m, --model` | str | None | HF model ID override (default `Arrexel/pattern-diffusion`) |
| `--pbr` | flag | `true` | When set, generate PBR maps via Materialize if the binary is available |
| `--no-pbr` | flag | `false` | Skip the automatic Materialize PBR step |
| `--cpu` | flag | `false` | Force CPU inference |
| `--low-vram` | flag | `false` | CPU offload (lower VRAM usage) |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |

> **Note:** When `--quality` is set, resolution and steps are auto-filled from the quality profile **only if** the user didn't explicitly pass `-W`, `-H`, `-s`, or `-g`. Explicit flags always win (soft resolution via `QualityEngine`).

### `texture2d presets`

List all available material presets with their prompts and recommended parameters.

```bash
texture2d presets
```

### `texture2d batch FILE`

Batch-generate textures from a prompts file (one prompt per line, `#` for comments).

```bash
texture2d batch prompts.txt -d textures/ --quality high
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d, --output-dir` | path | `outputs/textures/` | Output directory |
| `-p, --preset` | str | None | Default preset applied to all prompts |
| `-W, --width` | int | 1024 | Image width |
| `-H, --height` | int | 1024 | Image height |
| `-s, --steps` | int | 28 | Inference steps |
| `-g, --guidance` | float | 3.5 | Guidance scale |
| `-m, --model` | str | None | HF model ID override |
| `--quant` | str | `none` | Model quantization: `none`, `fp8`, `nf4` |
| `--no-pbr` | flag | `false` | Skip the automatic Materialize PBR step |
| `--low-vram` | flag | `false` | CPU offload (lower VRAM usage) |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier |

### `texture2d info`

Display configuration, system info (Python, PyTorch, CUDA, GPUs), HF cache location, and default output path.

```bash
texture2d info
```

### `texture2d skill install`

Install the Cursor Agent Skill (`SKILL.md`) into a game project's `.cursor/skills/texture2d/` directory.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Target project root directory |
| `--force` | flag | `false` | Overwrite existing skill file |

```bash
texture2d skill install -t /path/to/my-game --force
```

## Quality Presets

The `--quality` flag selects a preconfigured parameter profile. Profiles only fill defaults — explicitly provided flags (`-W`, `-H`, `-s`, `-g`) always take precedence.

| Profile | Resolution | Steps | Guidance | Description |
|---------|-----------|-------|----------|-------------|
| `fast` | 512×512 | 14 | 3.0 | Quick preview, minimum viable quality |
| `low` | 768×768 | 20 | 3.5 | Basic quality, faster generation |
| `medium` | 1024×1024 | 28 | 3.5 | Standard quality (**default**) |
| `high` | 1024×1024 | 40 | 4.0 | High quality, slower generation |
| `highest` | 2048×2048 | 50 | 4.5 | Maximum quality, longest generation |

### Material Presets

Each material preset overrides steps and guidance with curated values:

| Preset | Steps | Guidance | Category |
|--------|-------|----------|----------|
| Wood | 50 | 7.5 | Natural |
| Fabric | 50 | 7.5 | Natural |
| Metal | 60 | 8.0 | Industrial |
| Stone | 50 | 7.5 | Natural |
| Brick | 50 | 7.5 | Architectural |
| Leather | 50 | 7.5 | Natural |
| Concrete | 50 | 7.5 | Industrial |
| Marble | 60 | 8.0 | Architectural |
| Grass | 50 | 7.5 | Terrain |
| Sand | 50 | 7.5 | Terrain |
| Dirt | 50 | 7.5 | Terrain |
| Gravel | 50 | 7.5 | Terrain |
| Tile Floor | 50 | 7.5 | Architectural |

```bash
# Use a preset with quality-tier resolution
texture2d generate "scratched surface" --preset Metal --quality high -o metal.png
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face token (used to download the pattern-diffusion weights from the Hub) |
| `TEXTURE2D_MODEL_ID` | Override default model ID (`Arrexel/pattern-diffusion`) |
| `TEXTURE2D_BIN` | Override `texture2d` binary path (used by GameAssets) |
| `MATERIALIZE_BIN` | Override `materialize` binary path (used for the automatic PBR step) |

## Output Layout

```
outputs/
└── textures/
    ├── rough_stone_wall_surface_medieval_castle_1715000000.png
    └── rough_stone_wall_surface_medieval_castle_1715000000.json
```

- **PNG** — generated seamless texture image.
- **JSON** — metadata sidecar with `seed`, `prompt_final`, generation parameters, model info.
- Default output: `outputs/textures/`. Override with `-o` (generate) or `-d` (batch).

## Pipeline Integration

### Materialize (PBR maps)

By default `texture2d generate` runs the PBR step **automatically** when the `materialize` binary is on `PATH` (or `MATERIALIZE_BIN` is set): after producing the seamless diffuse texture, it invokes [Materialize](../Materialize/) to derive normal / height / metallic / roughness / ambient-occlusion maps into the same output directory.

```bash
# One command — diffuse + PBR (when Materialize is available)
texture2d generate "mossy stone" -o mossy_stone.png
# writes mossy_stone.png + mossy_stone_normal.png + mossy_stone_height.png + ...

# Explicit two-step flow (or when Materialize is not installed)
texture2d generate "mossy stone" --no-pbr -o diffuse.png
materialize diffuse.png --output-dir pbr/
```

If Materialize is **not** detected, `texture2d` emits a one-line notice and skips the PBR step (the diffuse texture is still produced). Install Materialize with `./install.sh materialize` at the monorepo root, or set `MATERIALIZE_BIN` to the binary path.

### GameAssets batch

[GameAssets](../GameAssets/) can use `texture2d` as the image source:

- In `game.yaml`, set `image_source: texture2d` (global) or per CSV row.
- With `texture2d.materialize: true` in the profile, GameAssets generates PBR maps automatically via Materialize.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

Use `TEXTURE2D_BIN` if the `texture2d` command is not on `PATH`.

## Development

```bash
cd Texture2D

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .

# Format
ruff format .
```

## Project Layout

```
Texture2D/
├── src/texture2d/
│   ├── __init__.py
│   ├── __main__.py        # python -m texture2d
│   ├── cli.py             # Click CLI (generate, batch, presets, info, skill)
│   ├── cli_rich.py        # Rich-click integration
│   ├── generator.py       # pattern-diffusion inference + Materialize PBR
│   ├── presets.py         # 13 material presets
│   ├── image_processor.py # Image saving + metadata
│   └── utils.py           # Helpers
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh
│   ├── installer.py
│   ├── install.sh
│   └── run_installer.sh
└── tests/
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights (default):** [Arrexel/pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) — **Apache-2.0** (StableDiffusion-2-base fine-tune on 6.8M tileable patterns). Read the model card before shipping or using in production.
- **Full license table:** [GameDev/README.md](../README.md) (Licenses section).
