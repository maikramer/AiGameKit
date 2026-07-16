# Texture2D — Seamless 2D Texture Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for **seamless (tileable) 2D textures** using **Stable Diffusion v1.5 + circular padding**, running locally on GPU.

Tiling is achieved **by construction**: every `Conv2d` layer in the UNet and VAE is patched to `padding_mode="circular"`, so the receptive field wraps around the image borders and the output tiles seamlessly in both axes — no LoRA, no post-processing, no trigger word. Uses [`stable-diffusion-v1-5/stable-diffusion-v1-5`](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) to generate textures that repeat without visible seams — ideal for floors, rocks, walls, and game-dev materials.

In the [GameDev](../README.md) monorepo, the package depends on [**gamedev-shared**](../Shared/) (`gamedev_shared`): quality presets, Rich CLI, GPU helpers, and shared conventions aligned with Text2D, Text3D, and GameAssets.

## Overview

- **Local GPU inference** — Stable Diffusion v1.5 + circular padding, no cloud API needed; fits in ~2.5 GB VRAM (a 6 GiB GPU is plenty)
- **Tiling by construction** — circular padding on all convolutions, no LoRA or post-processing
- **Real CFG** — negative prompts work natively (`--negative-prompt`), no `true-cfg` 2x cost
- **Automatic seamless prompting** — appends tileable/seamless instructions automatically
- **13 material presets** — Wood, Stone, Grass, Sand, Dirt, Metal, Brick, Fabric, Leather, Concrete, Marble, Gravel, Tile Floor
- **Quality tiers** — `fast`, `low`, `medium` (default), `high`, `highest` via `--quality`
- **Batch generation** — multiple textures from a prompt file
- **Multi-GPU** — `--gpu-ids 0,1` splits weights across GPUs via accelerate
- **JSON metadata** — each texture has a `.json` sidecar with seed, final prompt, and parameters
- **Hardware auto-detection** — `--hw-auto` detects device and multi-GPU layout (on by default)

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
| `texture2d generate PROMPT` | Generate a seamless texture (delegates to UMS when available) |
| `texture2d presets` | List available material presets |
| `texture2d batch FILE` | Batch generate from a prompt file (one per line) |
| `texture2d server` | **Deprecated** — use `gamedev-model-server start` (UMS) |
| `texture2d server-status` | **Deprecated** — use `gamedev-model-server status` |
| `texture2d server-stop` | **Deprecated** — use `gamedev-model-server stop` |
| `texture2d info` | Config, system, and environment info |
| `texture2d skill install` | Install Cursor Agent Skill |
| `texture2d validate-tileable` | Validate a texture's tileability |

### `texture2d generate PROMPT`

Generate a seamless tileable texture from a text prompt.

```bash
# Basic usage
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# With a material preset
texture2d generate "weathered surface" --preset Stone -o wall.png

# High quality with a fixed seed
texture2d generate "mossy cobblestone" --quality high --seed 42 -o cobble.png

# Native negative prompt (real CFG, no true-cfg cost)
texture2d generate "dark marble floor" -n "blurry, watermark" -o marble.png
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto (`outputs/textures/`) | Output file path (`.png`) |
| `-W, --width` | int | 512 | Image width (multiple of 8) |
| `-H, --height` | int | 512 | Image height (multiple of 8) |
| `-s, --steps` | int | 30 | Inference steps |
| `-g, --guidance` | float | 7.0 | Guidance scale (real CFG) |
| `--seed` | int | None | Random seed for reproducibility |
| `-n, --negative-prompt` | str | `""` | Negative prompt (works natively with SD1.5 CFG) |
| `-p, --preset` | str | None | Material preset (see Presets below) |
| `-m, --model` | str | None | HF model ID override (default: `stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `--cpu` | flag | `false` | Force CPU inference |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--hw-auto/--no-hw-auto` | flag | `on` | Hardware auto-detection (device + multi-GPU). No offload/clamp (SD1.5 fits any CUDA GPU) |
| `--ground` | str | `auto` | Top-down ground mode: applies viewpoint/lighting/scale prompt modifiers |
| `-v, --verbose` | flag | `false` | Verbose logging |

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
| `-W, --width` | int | 512 | Image width |
| `-H, --height` | int | 512 | Image height |
| `-s, --steps` | int | 30 | Inference steps |
| `-g, --guidance` | float | 7.0 | Guidance scale |
| `-m, --model` | str | None | HF model ID override |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier |
| `--hw-auto/--no-hw-auto` | flag | `on` | Hardware auto-detection |
| `--ground` | str | `auto` | Top-down ground mode |

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

### Unified Model Server (UMS)

Prefer **`gamedev-model-server`** (monorepo supervisor): one socket, smart VRAM
eviction, job queue with priority + affinity. `texture2d generate` auto-delegates
(and can auto-start the UMS unless `GAMEDEV_UMS_AUTO_START=0`).

```bash
gamedev-model-server start
texture2d generate "stone wall" -o stone.png
texture2d generate "wood" -o wood.png --ums-stream          # queue/progress events
texture2d generate "dirt" -o dirt.png --ums-priority batch
texture2d generate "test" -o t.png --no-ums                 # force in-process
gamedev-model-server queue
gamedev-model-server stop
```

| Flag | Description |
|------|-------------|
| `--ums-priority interactive\|batch` | Queue priority (default interactive / `GAMEDEV_UMS_PRIORITY`) |
| `--no-ums` | Skip UMS; run in-process |
| `--ums-stream` | Print UMS queue/progress NDJSON events |

Per-tool `texture2d server` remains only as a **deprecated** fallback. See [`ModelServer/README.md`](../ModelServer/README.md).

## Quality Presets

The `--quality` flag selects a preconfigured parameter profile. Profiles only fill defaults — explicitly provided flags (`-W`, `-H`, `-s`, `-g`) always take precedence.

| Profile | Resolution | Steps | Guidance | Description |
|---------|-----------|-------|----------|-------------|
| `fast` | 512×512 | 16 | 7.0 | Quick preview, minimum viable quality |
| `low` | 512×512 | 24 | 7.0 | Basic quality, faster generation |
| `medium` | 512×512 | 28 | 7.0 | Standard quality (**default**) |
| `high` | 768×768 | 32 | 7.0 | High quality, slower generation |
| `highest` | 1024×1024 | 40 | 7.0 | Maximum quality, longest generation |

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
| Grass | 30 | 7.0 | Terrain |
| Sand | 30 | 7.0 | Terrain |
| Dirt | 30 | 7.0 | Terrain |
| Gravel | 30 | 7.0 | Terrain |
| Tile Floor | 30 | 7.0 | Architectural |

```bash
# Use a preset with quality-tier resolution
texture2d generate "scratched surface" --preset Metal --quality high -o metal.png
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXTURE2D_MODEL_ID` | Override default SD model ID (`stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `TEXTURE2D_HW_AUTO` | Set to `0` to disable hardware auto-detection |
| `TEXTURE2D_BIN` | Override `texture2d` binary path (used by GameAssets) |

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

Generate a diffuse texture, then use [Materialize](../Materialize/) to create PBR maps (normal, height, metallic, roughness, ambient occlusion):

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```

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
│   ├── __main__.py            # python -m texture2d
│   ├── _validate_cli.py       # validate-tileable command
│   ├── cli.py                 # Click CLI (generate, batch, presets, server, info, skill)
│   ├── cli_rich.py            # Rich-click integration
│   ├── client.py              # Model server client
│   ├── cursor_skill/
│   │   └── SKILL.md           # Cursor Agent Skill
│   ├── generator.py           # SD1.5 + circular padding inference
│   ├── hardware.py            # Hardware auto-detection profile
│   ├── image_processor.py     # Image saving + metadata
│   ├── presets.py             # 13 material presets
│   ├── prompt_enhancer.py     # Ground/top-down prompt enhancers
│   ├── server.py              # Model server (keeps pipeline warm)
│   ├── tileability.py         # Tileability helpers
│   └── utils.py               # Helpers (validation, seeds, formatting)
├── config/
│   └── requirements-dev.txt   # Development dependencies
├── scripts/
│   └── installer.py           # System-wide installer
└── tests/
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights (default):** [stable-diffusion-v1-5/stable-diffusion-v1-5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) — CreativeML Open RAIL-M license; comply with the model's use restrictions.
- **Full license table:** [GameDev/README.md](../README.md) (Licenses section).
