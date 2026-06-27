# Materialize — PBR Map Generation

A **Rust CLI** that generates PBR (Physically Based Rendering) maps from diffuse/albedo textures using GPU compute shaders via [wgpu](https://wgpu.rs/). No GUI, no Unity — one command, six (or seven) maps.

Inspired by the original [Materialize](https://github.com/BoundingBoxSoftware/Materialize) by Bounding Box Software (Unity/Windows). This is a from-scratch Rust CLI that preserves the same concept — turning a single diffuse image into a complete PBR material set.

---

## Overview

Materialize takes a single diffuse/albedo texture and produces up to seven PBR maps:

| Map | Description |
|-----|-------------|
| **Height** | Surface elevation for parallax / displacement effects |
| **Normal** | Per-pixel surface normals for realistic lighting |
| **Metallic** | Metallic vs. dielectric mask |
| **Smoothness** | Inverse roughness (base + metallic + local-contrast contribution) |
| **Edge** | Edge detection derived from normals |
| **AO** | Ambient occlusion (cavity-style, derived from height) |
| **Curvature** _(opt-in)_ | Convex/concave curvature (Laplacian of height) — enable with `--include-curvature` |

**Key properties:**

- **Fast** — GPU compute shaders via wgpu; no CPU-bound image loops
- **Cross-platform** — Linux, macOS, Windows (Vulkan, Metal, DirectX 12)
- **No CUDA required** — wgpu works with any modern GPU
- **Auto-detect** — `-p auto` analyses the texture and picks the best preset
- **Batch-friendly** — directories and globs, `--skip-existing` resume
- **Scriptable** — CLI-only; stable exit codes

---

## Installation

### GameDev monorepo (recommended)

From the repository root:

```bash
./install.sh materialize
```

This compiles the crate and installs the binary to `~/.local/bin/`. Ensure `~/.local/bin` is on your `PATH`.

### Standalone build

Requires **Rust** 1.87+ and a GPU with up-to-date drivers.

```bash
cd Materialize
cargo build --release
cargo install --path .
```

The `materialize` binary is placed in Cargo's `~/.cargo/bin/`.

### Manual install script

```bash
git clone https://github.com/maikramer/Materialize-CLI.git
cd Materialize-CLI
./install.sh          # install
./install.sh reinstall
./install.sh uninstall
```

---

## Commands

### `materialize <INPUT>` (default command)

Generate PBR maps from a diffuse/albedo image. `INPUT` may be a single file, a
directory, or a glob pattern.

```bash
materialize texture.png
materialize texture.png -o ./out/ -v
materialize skin.png --preset skin -o ./materials/
materialize ./textures/ -o ./pbr/ --jobs 4 --progress
materialize texture.png -p auto -v
```

#### Options

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--output` | `-o` | path | `.` | Output directory |
| `--format` | `-f` | enum | `png` | Output format: `png`, `jpg`, `tga`, `exr` |
| `--preset` | `-p` | enum | `default` | Material preset (see below) |
| `--quality` | `-q` | int | `95` | JPEG quality 1–100 (ignored for other formats) |
| `--verbose` | `-v` | flag | — | Print progress, timings, auto-detect info |
| `--quiet` | — | flag | — | Suppress generated file list on success |
| `--include-curvature` | — | flag | — | Generate `texture_curvature.png` (7th map) |
| `--roughness` | — | flag | — | Output `texture_roughness.png` (= `1 - smoothness`) instead of `texture_smoothness.png` |
| `--normal-format` | — | enum | `opengl` | Normal map Y-axis convention: `opengl` (Y-up) or `directx` (Y-down) |
| `--only` | — | list | — | Whitelist maps: `height,normal,metallic,smoothness,edge,ao,curvature` |
| `--skip` | — | list | — | Blacklist maps (mutually exclusive with `--only`) |
| `--seamless` / `--no-seamless` | — | flag | auto | Force wrap or clamp sampling at borders |
| `--jobs` | — | int | `1` | CPU-side parallelism for batch (GPU stays serial) |
| `--skip-existing` | — | flag | — | Skip images whose height output already exists |
| `--progress` | — | flag | — | Show `[i/N]` per image during batch |
| `--list-presets` | — | flag | — | List all presets and exit |
| `--list-maps` | — | flag | — | List all generated map names and exit |
| `--generate-completions` | — | enum | — | Emit shell completion script: `bash`, `zsh`, `fish`, `elvish`, `powershell` |
| `--help` | `-h` | — | — | Show help message |
| `--version` | `-V` | — | — | Show version |

#### Inline overrides (apply on top of the preset)

`--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`,
`--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`,
`--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale`.

### `materialize info <image>`

Analyse a texture without generating maps. Prints the detected preset,
confidence score, and the full feature vector.

```bash
materialize info texture.png
```

### `materialize skill install`

Install the Materialize CLI [Cursor AI skill](.cursor/skills/materialize-cli/) into the current project's `.cursor/skills/materialize-cli/` directory.

```bash
materialize skill install
```

---

## PBR Presets

19 material presets plus `auto`. Use `-p` / `--preset` to select one.

| Preset | Description | Characteristics |
|--------|-------------|-----------------|
| `default` | General purpose | Balanced settings for any texture |
| `skin` | Human/character skin | No metallic, high smoothness, subtle normals |
| `floor` | Ground surfaces (stone, tile, dirt) | Pronounced height, strong AO, rough surface |
| `metal` | Metallic surfaces | Boosted metallic, sharp edges, polished look |
| `fabric` | Cloth / textile | Matte, no metallic, soft edges |
| `wood` | Wood grain | No metallic, moderate grain detail |
| `stone` | Rock / stone | Very rough, deep AO, strong normals |
| `concrete` | Concrete | Rough, gray, dense surface noise |
| `leather` | Leather | Pebbled, semi-smooth, warm tones |
| `marble` | Marble | Polished, veined, smooth |
| `sand` | Sand | Fine grain, very rough |
| `foliage` | Leaves / grass | Organic, low metallic, medium detail |
| `plaster` | Plaster / stucco | Flat, soft normals |
| `asphalt` | Asphalt | Dark, rough, dense edges |
| `brick` | Brick | Sharp edges, rough surface |
| `ice` | Ice | Very smooth, slight detail |
| `snow` | Snow | Soft, diffuse |
| `lava` | Lava | Molten, semi-metallic |
| `water` | Water | Very smooth, flowing |
| `auto` | Auto-detect | Analyse texture features and pick the best preset |

```bash
materialize brick_diffuse.png -p stone -o ./out/
materialize character_skin.png --preset skin -v
materialize metal_panel.jpg -p metal -f exr -o ./hdr/
materialize mystery.png -p auto -v
```

---

## Auto-detection (`-p auto`)

`-p auto` runs a fast CPU pre-pass over the texture and computes:

- Luminance mean/std
- Saturation mean/std
- Hue histogram (12 bins)
- Sobel edge density
- 5×5 local contrast variance
- Tile MSE (full top/bottom + left/right border rows/cols)
- Alpha coverage

A decision tree maps these features to a preset (metal, skin, wood, stone,
foliage, floor, default) plus a confidence score. When the tile MSE is below
`0.005`, the pipeline switches all neighbourhood-sampling shaders to wrap
(Euclidean modulo) at borders so the generated maps stay tileable.

Use `materialize info <image>` to preview the analysis without generating any
maps.

---

## Environment variables

| Variable | Values | Description |
|----------|--------|-------------|
| `MATERIALIZE_GPU_BACKEND` | `vulkan` · `metal` · `dx12` · `gl` · `primary` | Force a specific wgpu backend (default: `primary`) |
| `MATERIALIZE_LOG` | `error` · `warn` · `info` · `debug` · `trace` | Log level (default: `warn`) |

```bash
MATERIALIZE_GPU_BACKEND=vulkan materialize texture.png
MATERIALIZE_LOG=debug materialize texture.png -v
```

---

## Output Files

From an input file `texture.png`, Materialize generates up to seven files in
the output directory (extension follows `--format`):

| File | Description |
|------|-------------|
| `texture_height.{ext}` | Height / displacement map |
| `texture_normal.{ext}` | Normal map |
| `texture_metallic.{ext}` | Metallic map |
| `texture_smoothness.{ext}` or `texture_roughness.{ext}` | Smoothness (default) or roughness (`--roughness`) |
| `texture_edge.{ext}` | Edge detection map |
| `texture_ao.{ext}` | Ambient occlusion map |
| `texture_curvature.{ext}` | Curvature map (only with `--include-curvature`) |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — files generated |
| `1` | Generic error |
| `2` | Input file not found |
| `3` | Unsupported input format |
| `4` | GPU error (no adapter, device creation failed, …) |
| `5` | I/O error (permissions, disk full, …) |
| `6` | Image too large for GPU |

---

## Pipeline Integration

Materialize runs **after** Texture2D or Paint3D to generate PBR maps from diffuse textures. It is integrated into the GameDev monorepo pipeline at several points:

- **GameAssets batch** — via `materialize: true` in the `texture2d` block of `game.yaml`
- **Paint3D** — `vertex-pbr` command uses Materialize for map generation
- **Standalone** — can process any diffuse image from any source

Uses wgpu compute shaders for cross-platform GPU acceleration — no CUDA required. Works on Linux (Vulkan), macOS (Metal), and Windows (DirectX 12) with any modern GPU.

```bash
# Typical pipeline: generate texture, then PBR maps
texture2d generate "brick wall" -o ./textures/
materialize textures/brick_wall.png -p auto -o ./materials/brick/
```

### Batch processing

```bash
# Process a directory (sequentially; --jobs controls CPU-side parallelism)
materialize ./textures/ -o ./pbr/ --jobs 4 --progress

# Resume after interruption
materialize ./textures/ -o ./pbr/ --skip-existing

# Glob pattern
materialize "./textures/bricks/*.png" -o ./pbr/

# Script-friendly: quiet mode, check exit code
materialize texture.png -o ./out/ --quiet
if [ $? -eq 0 ]; then echo "PBR maps generated"; fi
```

---

## Development

```bash
cd Materialize

# Build
cargo build

# Run tests
cargo test

# Format (auto-fix)
cargo fmt

# Lint
cargo clippy -- -D warnings
```

Requires **Rust** 1.87+ (edition 2024). Dev dependencies: `tempfile` for integration tests.

---

## License & Attribution

**MIT License** — see [LICENSE](LICENSE).

This project is based on [Materialize](https://github.com/BoundingBoxSoftware/Materialize) by **Bounding Box Software**. The original Materialize is a Unity-based Windows application for generating PBR maps. This is a from-scratch Rust reimplementation that preserves the concept and algorithmic approach.

---

## Documentation

- [docs/README.md](docs/README.md) — Overview, installation details, and doc index
- [docs/cli-api.md](docs/cli-api.md) — Full CLI reference, environment variables, shell completion
- [docs/architecture.md](docs/architecture.md) — System structure
- [docs/features.md](docs/features.md) — Capabilities
- [docs/algorithms.md](docs/algorithms.md) — Processing algorithms
- [docs/shaders.md](docs/shaders.md) — WGSL compute shaders
- [docs/roadmap.md](docs/roadmap.md) — Future plans
- [CHANGELOG.md](CHANGELOG.md) — Release history (2.0 breaking changes)

## Getting Help

- **Bugs & features** — [Open an issue](https://github.com/maikramer/Materialize-CLI/issues)
- **Questions** — [GitHub Discussions](https://github.com/maikramer/Materialize-CLI/discussions)
- **Contributing** — See [CONTRIBUTING.md](CONTRIBUTING.md)
