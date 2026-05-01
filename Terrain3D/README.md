# Terrain3D — AI Terrain Generation

> Diffusion-based terrain heightmap generation using [terrain-diffusion](https://github.com/millennium-nova/terrain-diffusion) (MIT).
> Produces grayscale heightmap PNG + terrain JSON compatible with [VibeGame](../VibeGame/) and [GameAssets](../GameAssets/).

Uses a diffusion model trained on real-world elevation data (WorldClim + ETOPO) to produce realistic terrain with mountains, valleys, and ridges — no manual editing needed.

## Overview

- **AI terrain** — diffusion-based heightmap generation (~30 m resolution)
- **Seed reproducibility** — same seed → same terrain
- **Heightmap PNG** — 8-bit grayscale, normalized 0–1
- **JSON metadata** — version 2.0, compatible with VibeGame/GameAssets pipeline
- **WorldClim conditioning** — synthetic bioclim maps for realistic elevation distributions
- **Auto-download** — WorldClim bioclim rasters fetched on first run
- **Quality presets** — five tiers (`fast` / `low` / `medium` / `high` / `highest`) via QualityEngine

## Requirements

- Python 3.10+
- PyTorch 2.4+ (CUDA required)
- ~6 GB VRAM
- Network access (model + WorldClim download on first run)

## Installation

### Official (monorepo)

From the **GameDev** repo root:

```bash
cd Shared && pip install -e .
cd Terrain3D && pip install -e .
```

Or use the unified installer:

```bash
./install.sh terrain3d
```

General guide: [`docs/INSTALLING.md`](../docs/INSTALLING.md).

### Manual / development

```bash
cd Terrain3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
terrain3d --help
```

## Commands

### `terrain3d generate`

Generate an AI terrain heightmap via diffusion.

```bash
# Default quality (medium, 2048×2048)
terrain3d generate --seed 42

# Fast preview
terrain3d generate --seed 100 --quality fast

# Custom resolution and scale
terrain3d generate --size 1024 --max-height 100 --world-size 1024 --output my_terrain.png

# Half-precision for lower VRAM
terrain3d generate --seed 7 --dtype bf16

# Quiet mode (paths only on stdout, good for scripting)
terrain3d generate --seed 99 --quiet
```

### CLI Reference

**Entry:** `terrain3d generate` / `python -m terrain3d generate`

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--prompt` | str | `None` | Terrain description (stored as metadata; model is unconditional) |
| `--seed` | int | random | Random seed for reproducibility |
| `-o, --output` | path | `heightmap.png` | Heightmap PNG output path |
| `--metadata` | path | `terrain.json` | JSON metadata output path |
| `--size` | int | 2048 | Heightmap resolution (px) |
| `--world-size` | float | 512.0 | World extent in meters (X/Z) |
| `--max-height` | float | 50.0 | Max terrain height in meters |
| `--quality` | str | `medium` | Quality tier (`fast`/`low`/`medium`/`high`/`highest`) |
| `--device` | str | auto | Device override (`cuda`, `cpu`) |
| `--dtype` | str | `fp32` | Model precision (`fp32`, `bf16`, `fp16`) |
| `--cache-size` | str | `100M` | Tile cache size (e.g. `100M`, `1G`) |
| `--coarse-window` | int | 4 | Number of coarse tiles (~7.7 km each) |
| `--quiet` | flag | false | Suppress progress output |

Quality presets control `--size`, `--world-size`, and `--coarse-window` via [QualityEngine](../Shared/). User-provided values always take precedence (soft resolution).

### Other commands

```bash
terrain3d --help        # Show all commands
terrain3d --version     # Print version
```

## Quality Presets

| Profile | Size | World Size | Coarse Window |
|---------|------|------------|---------------|
| `fast` | 512 | 256 m | 2 |
| `low` | 1024 | 256 m | 3 |
| `medium` (default) | 2048 | 512 m | 4 |
| `high` | 4096 | 512 m | 6 |
| `highest` | 4096 | 1024 m | 8 |

Values sourced from [`quality-profiles.yaml`](../Shared/src/gamedev_shared/data/quality-profiles.yaml).

```bash
# Use a preset — overrides size/world-size/coarse-window unless user specified them
terrain3d generate --quality high --seed 42
```

## Output Layout

### `heightmap.png`

8-bit grayscale PNG. Pixel values 0–255 map to elevation 0–1 (normalized).

### `terrain.json`

```json
{
  "version": "2.0",
  "generator": "terrain3d",
  "model_id": "xandergos/terrain-diffusion-30m",
  "terrain": {
    "size": 1024,
    "world_size": 512.0,
    "max_height": 50.0,
    "height_min": 0.0,
    "height_max": 1.0,
    "height_mean": 0.56,
    "height_std": 0.18
  },
  "rivers": [],
  "lakes": [],
  "lake_planes": [],
  "stats": {
    "generation_time_seconds": 108.2
  }
}
```

## Pipeline Integration

[GameAssets](../GameAssets/) can call `terrain3d` during batch generation or via `gameassets dream`. Set `TERRAIN3D_BIN` if the command is not on `PATH`.

Output heightmaps feed into VibeGame's [`<Terrain>`](../VibeGame/) recipe via the `url` attribute:

```html
<Terrain url="/assets/terrain/heightmap.png" world-size="512" max-height="50"></Terrain>
```

> **Note:** The terrain-diffusion model is vendored under `src/terrain3d/vendor/` (MIT). Requires a CUDA GPU.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TERRAIN3D_BIN` | Path to `terrain3d` binary (for GameAssets pipeline) |
| `TERRAIN3D_MODEL_ID` | Override default model (`xandergos/terrain-diffusion-30m`) |
| `HF_HOME` | Hugging Face cache directory |

## Project Layout

```
Terrain3D/
├── src/terrain3d/
│   ├── cli.py                 # Click CLI
│   ├── cli_rich.py            # Rich-click + theme
│   ├── generator.py           # WorldPipeline wrapper
│   ├── export.py              # PNG + JSON export
│   └── vendor/                # Vendored terrain-diffusion code (MIT)
│       ├── inference/         # WorldPipeline, synthetic maps, postprocessing
│       ├── models/            # EDM UNet, MP layers
│       ├── scheduler/         # DPM-Solver scheduler
│       ├── data/              # Laplacian encoder
│       ├── common/            # Shared helpers
│       └── data/global/       # WorldClim + ETOPO rasters
├── scripts/
│   └── installer.py           # Package installer
├── tests/
├── pyproject.toml
└── THIRD_PARTY.md             # Vendored code licenses
```

## Development

```bash
cd Terrain3D && pip install -e ".[dev]"
pytest tests/ -v
ruff check .
ruff format .
```

Run from monorepo root:

```bash
make test-terrain3d
```

## License

- **Code:** MIT — [`LICENSE`](LICENSE).
- **Vendored code (terrain-diffusion):** MIT — [`THIRD_PARTY.md`](THIRD_PARTY.md).
- **Model weights:** [xandergos/terrain-diffusion-30m](https://huggingface.co/xandergos/terrain-diffusion-30m) — check the model card for license terms.
- **WorldClim data:** [worldclim.org](https://worldclim.org/) — free for research and non-commercial use.
