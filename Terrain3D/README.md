# Terrain3D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

AI-powered terrain generation via [terrain-diffusion](https://github.com/millennium-nova/terrain-diffusion) (MIT). Generates heightmap PNG + terrain JSON compatible with [VibeGame](../VibeGame/) and [GameAssets](../GameAssets/).

Uses a diffusion model trained on real-world elevation data (WorldClim + ETOPO) to produce realistic terrain with mountains, valleys, and ridges — no manual editing needed.

## Features

- **AI terrain** — diffusion-based heightmap generation (~30 m resolution)
- **Seed reproducibility** — same seed → same terrain
- **Heightmap PNG** — 8-bit grayscale, normalized 0–1
- **JSON metadata** — version 2.0, compatible with VibeGame/GameAssets pipeline
- **WorldClim conditioning** — synthetic bioclim maps for realistic elevation distributions
- **Auto-download** — WorldClim bioclim rasters fetched on first run

## Requirements

- Python 3.10+
- PyTorch 2.4+ (CUDA required)
- ~6 GB VRAM
- Network access (model download on first run)

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh terrain3d
```

General guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development

```bash
cd Terrain3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
terrain3d --help
```

## Usage

### Generate terrain

```bash
terrain3d generate --seed 42 --size 1024
terrain3d generate --seed 100 --size 2048 --output my_terrain.png
terrain3d generate --size 1024 --max-height 100 --world-size 1024
```

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--seed` | random | Random seed for reproducibility |
| `--size` | 2048 | Heightmap resolution in pixels |
| `--output` | `heightmap.png` | Heightmap PNG output path |
| `--metadata` | `terrain.json` | JSON metadata output path |
| `--world-size` | 512.0 | World extent in meters (X/Z) |
| `--max-height` | 50.0 | Max terrain height in meters |
| `--device` | auto | Device (`cuda`, `cpu`) |
| `--dtype` | fp32 | Model precision (`fp32`, `bf16`, `fp16`) |
| `--cache-size` | 100M | Tile cache size |
| `--coarse-window` | 4 | Number of coarse tiles (~7.7 km each) |
| `--prompt` | none | Terrain description (metadata only; model is unconditional) |
| `--quiet` | off | Suppress progress output |

### Info

```bash
terrain3d --help
terrain3d --version
```

## Output

### heightmap.png

8-bit grayscale PNG. Pixel values 0–255 map to elevation 0–1 (normalized).

### terrain.json

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

## Layout

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

## Environment variables

| Variable | Description |
|----------|-------------|
| `TERRAIN3D_MODEL_ID` | Override default model (`xandergos/terrain-diffusion-30m`) |
| `TERRAIN3D_BIN` | Path to `terrain3d` binary (for GameAssets) |
| `HF_HOME` | Hugging Face cache directory |

## GameAssets integration

[GameAssets](../GameAssets/) can call `terrain3d` during batch generation. Use `TERRAIN3D_BIN` if the command is not on `PATH`.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Vendored code (terrain-diffusion):** MIT — [THIRD_PARTY.md](THIRD_PARTY.md).
- **Model weights:** [xandergos/terrain-diffusion-30m](https://huggingface.co/xandergos/terrain-diffusion-30m) — check the model card for license terms.
- **WorldClim data:** [worldclim.org](https://worldclim.org/) — free for research and non-commercial use.
