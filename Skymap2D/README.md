# Skymap2D — Equirectangular 360° Skymap Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for generating **equirectangular 360° skymaps** using FLUX.1-dev + LoRA, locally on GPU.

Uses the [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) model to produce full-dome panoramas usable as skybox/skymap environment maps in game engines — ideal for skies, outdoor environments, and atmospheric backgrounds.

In the [GameDev](../README.md) monorepo, the package depends on [**gamedev-shared**](../Shared/) (`gamedev_shared`): Rich CLI, quality presets, multi-GPU, logging, and helpers aligned with Text2D / Texture2D / GameAssets.

## Overview

- **Local GPU inference** — FLUX.1-dev + LoRA via `diffusers` (CUDA required)
- **Automatic equirectangular prompting** — appends 360° / equirectangular instructions automatically
- **10 environment presets** — Sunset, Night Sky, Overcast, Clear Day, Storm, Space, and more
- **Batch generation** — multiple skymaps from a prompt file
- **JSON metadata** — each skymap has a `.json` sidecar with seed, final prompt, and parameters
- **2:1 aspect ratio** — defaults tuned (2048×1024) for standard equirectangular projection
- **EXR output (optional)** — RGB float32 linear-space OpenEXR for engines that prefer `.exr`
- **Quality presets** — 5 tiers (`fast` → `highest`) via QualityEngine
- **Multi-GPU** — split LoRA weights across GPUs with `--gpu-ids`

## Installation

### Monorepo (recommended)

```bash
cd Shared && pip install -e .
cd Skymap2D && pip install -e .
```

Requires **CUDA GPU** and Python 3.10+.

### Unified installer

```bash
./install.sh skymap2d
# or: python3 -m gamedev_shared.installer.unified skymap2d
```

### Dev dependencies

```bash
cd Skymap2D && pip install -e ".[dev]"
```

## Commands

```
skymap2d generate PROMPT   Generate a 360° equirectangular skymap
skymap2d presets           List available sky presets
skymap2d batch FILE        Batch from prompts file (one per line)
skymap2d info              Config + environment info
skymap2d skill install     Install Cursor Agent Skill
```

**Global flags:** `--verbose` / `-v` — detailed logs.

### `skymap2d generate PROMPT`

Generate an equirectangular 360° skymap image.

```bash
# Basic generation
skymap2d generate "sunset over mountains, warm golden light" -o sky_sunset.png

# Use a preset
skymap2d generate "dramatic sky" --preset Storm -o sky_storm.png

# EXR output (linear RGB float)
skymap2d generate "clear blue sky" --format exr -o sky_clear.exr

# High quality with explicit seed
skymap2d generate "nebula" --quality high --seed 42 -o sky_nebula.png

# Low VRAM mode
skymap2d generate "alien planet" --low-vram -o sky_alien.png
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | `outputs/skymaps/` | Output file path (`.png` or `.exr`) |
| `-W, --width` | int | `2048` | Image width |
| `-H, --height` | int | `1024` | Image height |
| `-s, --steps` | int | `28` | Inference steps |
| `-g, --guidance-scale` | float | `3.5` | Guidance scale |
| `--seed` | int | None | Reproducible seed (random if omitted) |
| `-n, --negative-prompt` | str | `""` | Negative prompt |
| `-p, --preset` | str | None | Sky preset name (see below) |
| `--cfg-scale` | float | None | CFG scale override (defaults to guidance) |
| `--lora-strength` | float | `1.0` | LoRA strength (0.0–2.0) |
| `-m, --model` | str | None | LoRA model ID override (default: `Flux-LoRA-Equirectangular-v3`) |
| `--cpu` | flag | false | Run on CPU only |
| `--low-vram` | flag | false | Low VRAM mode (CPU offload) |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `0,1`) |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--format` | str | `png` | Output format: `png` (8-bit sRGB) or `exr` (RGB float linear) |
| `--exr-scale` | float | `1.0` | Multiply linear values when writing EXR |

### `skymap2d presets`

List all available sky presets with their base prompts and parameters.

```bash
skymap2d presets
```

### `skymap2d batch FILE`

Generate multiple skymaps from a text file (one prompt per line, `#` comments ignored).

```bash
skymap2d batch prompts.txt --output-dir skies/ --quality high
```

Supports all generation flags (`--width`, `--height`, `--steps`, `--guidance-scale`, `--preset`, `--quality`, `--format`, `--exr-scale`, `--cpu`, `--low-vram`, `--gpu-ids`).

### `skymap2d info`

Display configuration and system information (model IDs, CUDA status, GPU VRAM, Python version).

```bash
skymap2d info
```

### `skymap2d skill install`

Install the Cursor Agent Skill file into a game project.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Target directory (creates `.cursor/skills/skymap2d/`) |
| `--force` | flag | false | Overwrite existing skill file |

```bash
skymap2d skill install --target /path/to/game --force
```

## Quality Presets

The `--quality` flag selects a tier that sets resolution, steps, and guidance (only when the user hasn't explicitly provided those values — soft resolution via `QualityEngine`).

| Profile | Resolution | Steps | Guidance |
|---------|-----------|-------|----------|
| `fast` | 1024×512 | 8 | 3.5 |
| `low` | 1024×512 | 14 | 3.5 |
| `medium` | 2048×1024 | 28 | 3.5 |
| `high` | 2048×1024 | 40 | 3.5 |
| `highest` | 4096×2048 | 50 | 3.5 |

```bash
# Fast preview
skymap2d generate "sunset" --quality fast -o preview.png

# Production quality
skymap2d generate "sunset" --quality highest -o production.png
```

## Presets

10 built-in environment presets that set prompt, negative prompt, guidance scale, steps, and resolution:

| Name | Description | Guidance | Steps |
|------|-------------|----------|-------|
| Sunset | Golden sunset, warm clouds | 6.0 | 40 |
| Night Sky | Starry night, Milky Way | 6.5 | 45 |
| Overcast | Cloudy sky, diffuse light | 6.0 | 40 |
| Clear Day | Clear blue sky, few clouds | 6.0 | 40 |
| Storm | Storm, dark clouds, lightning | 7.0 | 50 |
| Space | Outer space, nebula, stars | 6.5 | 45 |
| Alien World | Alien sky, two moons, fantasy colors | 7.0 | 50 |
| Dawn | Dawn, pink and orange tones | 6.0 | 40 |
| Underwater | Underwater view, light rays, water | 6.5 | 45 |
| Fantasy | Magic sky, auroras, floating crystals | 7.0 | 50 |

```bash
skymap2d generate "dramatic clouds" --preset Storm -o storm_sky.png
skymap2d generate "cosmic" --preset Space --quality high -o space_sky.png
```

## Notes

### Equirectangular correction

The HF model ([Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3)) may return images in the **wrong resolution** (e.g. 1024×768 instead of the requested 2048×1024) with poles placed at the **center** of the vertical axis instead of at the top/bottom edges. Skymap2D automatically:

1. **Resizes** to the correct 2:1 equirectangular ratio.
2. Applies a **50% vertical shift** to move poles from center to edges.

### Three.js / VibeGame integration

The Three.js PMREM convention maps equirectangular textures as:

- `u = atan(dir.z, dir.x)` — horizontal azimuth
- `v = asin(dir.y)` — vertical elevation

Center of the image = horizon, top = zenith, bottom = nadir. Portrait images or images with swapped axes may produce "pillar" artifacts. Normalize to 2:1 landscape before PMREM.

For VibeGame integration, use `applyEquirectSkyEnvironment()` from `vibegame` (`VibeGame/src/extras/sky-env.ts`), which handles PMREM generation from equirectangular sources.

### EXR format

The model generates LDR (sRGB) content. When `--format exr` is used, Skymap2D writes **linear RGB float32** OpenEXR without a second sRGB curve. The `--exr-scale` multiplier allows boosting intensity (e.g. `2.0` for brighter skies). We do **not** use [Materialize](../Materialize/) here — that pipeline generates PBR maps (normal, height, etc.) from textures.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SKYMAP2D_BIN` | Override `skymap2d` binary path |
| `SKYMAP2D_MODEL_ID` | Override default LoRA model ID |
| `HF_TOKEN` | Hugging Face token (or `HUGGINGFACEHUB_API_TOKEN`) |
| `CUDA_VISIBLE_DEVICES` | Restrict visible GPUs |

## Output Layout

By default, generated skymaps go to `outputs/skymaps/`:

```
outputs/
  skymaps/
    sunset_over_mountains_1719876543.png
    sunset_over_mountains_1719876543.json   # metadata sidecar
    storm_sky_1719876600.exr
    storm_sky_1719876600.json
```

The JSON sidecar contains: seed, prompt (final with equirectangular suffix), guidance, steps, width, height, model IDs, and timestamp.

## Pipeline Integration

Skymap2D generates equirectangular sky images for use as environment maps in 3D engines. In the GameAssets pipeline, it is triggered by `gameassets dream` (with `--with-sky`) and produces HDR/LDR sky images that feed into VibeGame's `applyEquirectSkyEnvironment()`.

```bash
# Via GameAssets pipeline
gameassets handoff --public-dir public/   # copies sky images to the game project
```

### Engine compatibility

| Engine | How to use |
|--------|-----------|
| **VibeGame** | `applyEquirectSkyEnvironment()` from `vibegame` — handles PMREM |
| **Godot** | Environment → Sky → PanoramaSky → assign panorama texture |
| **Unity** | Skybox material with Panoramic shader → assign texture |
| **Unreal Engine** | Sky Sphere → equirectangular texture map |

## Development

```bash
cd Skymap2D

# Install (with dev deps)
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .

# Format
ruff format .
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights (default):** [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — LoRA on [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) (**non-commercial** BFL license).
- **Full license table:** [GameDev/README.md](../README.md) (Licenses section).
