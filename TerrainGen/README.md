# TerrainGen

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for **procedural terrain generation** — diamond-square heightmaps, hydraulic erosion, flow-based rivers, and lake placement.

Generates 8-bit grayscale heightmap PNGs (2048x2048) and JSON metadata with river paths, lake positions, and generation stats. Designed for the VibeGame 3D engine and the `gameassets dream` pipeline.

In the [GameDev](../README.md) monorepo, the package depends on [**gamedev-shared**](../Shared/) (`gamedev_shared`): Rich CLI, logging, and helpers aligned with other GameDev tools.

## Features

- **Diamond-square** — fractal heightmap generation with configurable roughness, ported from [grkvlt/landscape](https://github.com/grkvlt/landscape)
- **Gradient smoothing** — flattens low-gradient areas into natural plains
- **Hydraulic erosion** — particle-based simulation that carves valleys and deposits sediment (Ivo van der Veen algorithm)
- **River extraction** — D8 flow accumulation via [whitebox](https://github.com/jblindsay/whitebox-tools) with valley carving
- **Lake generation** — depression identification (Planchon-Darboux) with lake-plane decomposition for VibeGame
- **Deterministic** — same seed produces byte-identical output every time

## Quick start

```bash
# 1. Setup (venv + deps)
./scripts/setup.sh

# 2. Activate
source .venv/bin/activate

# 3. Generate a terrain
terraingen generate --seed 42 -o heightmap.png --metadata terrain.json

# 4. Generate with a prompt (stored as metadata for gameassets dream)
terraingen generate --prompt "mountain island" --seed 42
```

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh terraingen
# Windows: .\install.ps1 terraingen
```

The installer **creates** `TerrainGen/.venv` if missing, editable install, and wrappers in `~/.local/bin`. Tool list: `./install.sh --list`. General guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development

```bash
cd TerrainGen
pip install -e ".[dev]"
```

Dependencies: `numpy`, `pillow`, `whitebox`, `tifffile`, `click`, `rich`, `rich-click`, `gamedev-shared`.

## Commands

| Command | Description |
|---------|-------------|
| `terraingen generate` | Generate a procedural terrain heightmap |

## `generate` options

| Option | Default | Description |
|--------|---------|-------------|
| `--prompt` | None | Terrain description (stored as metadata) |
| `--seed` | random | Random seed for reproducibility |
| `--output/-o` | heightmap.png | Heightmap PNG output path |
| `--metadata` | terrain.json | JSON metadata output path |
| `--size` | 2048 | Heightmap resolution (px) |
| `--world-size` | 256.0 | World size in meters |
| `--max-height` | 50.0 | Max terrain height in meters |
| `--roughness` | 2.0 | Diamond-square roughness |
| `--erosion-particles` | 50000 | Number of erosion particles |
| `--river-threshold` | 1000 | Flow accumulation threshold for rivers |
| `--no-erosion` | off | Skip erosion step |
| `--no-rivers` | off | Skip river extraction |
| `--no-lakes` | off | Skip lake generation |
| `--quiet` | off | Suppress progress output |

### Examples

```bash
# Minimal: random seed, default settings
terraingen generate

# Deterministic with custom resolution
terraingen generate --seed 42 --size 1024 --world-size 128 --max-height 30

# Fast preview (skip heavy steps)
terraingen generate --no-erosion --no-rivers --no-lakes --quiet

# High-detail with erosion tuning
terraingen generate --seed 7 --erosion-particles 100000 --river-threshold 500
```

## Pipeline

The generation pipeline runs in order:

1. **Diamond-square** — fractal heightmap from a small grid, doubling each iteration
2. **Smoothing** — gradient-based low-pass filter, flattens plains while keeping ridges
3. **Erosion** (optional) — particle simulation: drops at high points, erodes/deposits sediment, evaporates
4. **Rivers** (optional) — D8 flow accumulation, extraction above threshold, Gaussian valley carving
5. **Lakes** (optional) — depression filling, connected-component filtering, lake-plane decomposition
6. **Export** — 8-bit grayscale PNG + JSON metadata

## Output

Two files per generation:

- **Heightmap PNG** — 8-bit grayscale, `size x size` pixels, mode `L`. Row 0 = north. VibeGame reads the R channel and maps `pixel/255 * max_height` to world-space elevation.
- **JSON metadata** — river paths (pixel + world coordinates), lake positions and depths, lake planes for VibeGame, generation stats per step.

### JSON schema (excerpt)

```json
{
  "version": "1.0",
  "terrain": { "size": 2048, "world_size": 256, "max_height": 50 },
  "rivers": [{ "id": 0, "source": [1024, 512], "world_path": [[128.0, 64.0], ...] }],
  "lakes": [{ "id": 0, "center_world": [100.0, 75.0], "surface_height": 22.5 }],
  "lake_planes": [{ "lake_id": 0, "pos_x": 100.0, "pos_y": 22.5, "pos_z": 75.0, "size_x": 20.0, "size_z": 15.0 }],
  "stats": { "generation_time_seconds": 12.5, "steps": { ... } }
}
```

## VibeGame integration

Use the generated heightmap and metadata in VibeGame world XML:

```html
<Terrain heightmap="/assets/terrain/heightmap.png"
         world-size="256" max-height="50"
         terrain-data-url="/assets/terrain/terrain.json"></Terrain>
```

The `terrain-data-url` attribute triggers the JSON data loader, which spawns `<Water>` entities for rivers and lakes.

## GameAssets integration

The `gameassets dream` pipeline can generate terrain automatically:

```bash
gameassets dream "mountain island with rivers and lakes" --terrain --terrain-prompt "mountain island"
```

Use `TERRAINGEN_BIN` if the `terraingen` command is not on `PATH`.

## Configuration

| Variable | Description |
|----------|-------------|
| `TERRAINGEN_BIN` | Path to `terraingen` binary (if not on `PATH`) |

## Layout

```
TerrainGen/
├── src/terraingen/
│   ├── cli.py             # Click CLI (generate command, Rich progress)
│   ├── cli_rich.py        # Rich-click integration
│   ├── heightmap.py       # Diamond-square + gradient smoothing
│   ├── erosion.py         # Particle-based hydraulic erosion
│   ├── rivers.py          # Flow accumulation + river extraction + valley carving
│   ├── lakes.py           # Depression filling + lake generation
│   ├── pipeline.py        # Full pipeline orchestration
│   └── export.py          # PNG + JSON export
├── scripts/
│   └── installer.py       # Delegates to gamedev-shared unified installer
└── tests/
```

## Algorithm references

- **Diamond-square:** [grkvlt/landscape](https://github.com/grkvlt/landscape) (Apache 2.0, Java)
- **Hydraulic erosion:** Ivo van der Veen, ["Improved Terrain Generation Using Hydraulic Erosion"](https://medium.com/@ivo.thom.vanderveen/improved-terrain-generation-using-hydraulic-erosion-2adda8e3d99b)
- **Hydrology:** [whitebox-tools](https://github.com/jblindsay/whitebox-tools) (MIT), D8 flow accumulation, Planchon-Darboux depression filling

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Full table:** [GameDev/README.md](../README.md) (Licenses section).
