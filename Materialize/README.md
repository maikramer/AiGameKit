# Materialize — PBR Map Generation

A **Rust CLI** that generates PBR (Physically Based Rendering) maps from diffuse/albedo textures using GPU compute shaders via [wgpu](https://wgpu.rs/). No GUI, no Unity — one command, six maps.

Inspired by the original [Materialize](https://github.com/BoundingBoxSoftware/Materialize) by Bounding Box Software (Unity/Windows). This is a from-scratch Rust CLI that preserves the same concept — turning a single diffuse image into a complete PBR material set.

---

## Overview

Materialize takes a single diffuse/albedo texture and produces six PBR maps:

| Map | Description |
|-----|-------------|
| **Height** | Surface elevation for parallax / displacement effects |
| **Normal** | Per-pixel surface normals for realistic lighting |
| **Metallic** | Metallic vs. dielectric mask |
| **Smoothness** | Inverse roughness (base + metallic contribution) |
| **Edge** | Edge detection derived from normals |
| **AO** | Ambient occlusion (cavity-style, derived from height) |

**Key properties:**

- **Fast** — GPU compute shaders via wgpu; no CPU-bound image loops
- **Cross-platform** — Linux, macOS, Windows (Vulkan, Metal, DirectX 12)
- **No CUDA required** — wgpu works with any modern GPU
- **Scriptable** — CLI-only; easy to batch and integrate into pipelines

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
# Clone and run installer (places binary in ~/.local/bin)
git clone https://github.com/maikramer/Materialize-CLI.git
cd Materialize-CLI
./install.sh          # install
./install.sh reinstall
./install.sh uninstall
```

---

## Commands

### `materialize <INPUT>` (default command)

Generate PBR maps from a diffuse/albedo image.

```bash
materialize texture.png
materialize texture.png -o ./out/ -v
materialize skin.png --preset skin -o ./materials/
materialize diffuse.png --format png --quiet
```

#### Options

| Flag | Short | Type | Default | Description |
|------|-------|------|---------|-------------|
| `--output` | `-o` | path | `.` | Output directory |
| `--format` | `-f` | str | `png` | Output format: `png`, `jpg`, `jpeg`, `tga`, `exr` |
| `--preset` | `-p` | str | `default` | Material preset (see below) |
| `--quality` | `-q` | int | `95` | JPEG quality 0–100 (ignored for other formats) |
| `--verbose` | `-v` | flag | — | Print progress and timing information |
| `--quiet` | — | flag | — | Suppress generated file list on success |
| `--help` | `-h` | — | — | Show help message |
| `--version` | `-V` | — | — | Show version (`materialize-cli 1.0.0`) |

### `materialize skill install`

Install the Materialize CLI [Cursor AI skill](.cursor/skills/materialize-cli/) into the current project's `.cursor/skills/materialize-cli/` directory.

```bash
materialize skill install
```

---

## PBR Presets

Presets tune the generation parameters for specific material types. Use `-p` / `--preset` to select one.

| Preset | Description | Characteristics |
|--------|-------------|-----------------|
| `default` | General purpose | Balanced settings for any texture |
| `skin` | Human/character skin | No metallic, high smoothness, subtle normals |
| `floor` | Ground surfaces (stone, tile, dirt) | Pronounced height, strong AO, rough surface |
| `metal` | Metallic surfaces | Boosted metallic, sharp edges, polished look |
| `fabric` | Cloth / textile | Matte, no metallic, soft edges |
| `wood` | Wood grain | No metallic, moderate grain detail |
| `stone` | Rock / stone | Very rough, deep AO, strong normals |

```bash
materialize brick_diffuse.png -p stone -o ./out/
materialize character_skin.png --preset skin -v
materialize metal_panel.jpg -p metal -f exr -o ./hdr/
```

---

## Output Files

From an input file `texture.png`, Materialize generates six files in the output directory (extension follows `--format`):

| File | Description |
|------|-------------|
| `texture_height.{ext}` | Height / displacement map |
| `texture_normal.{ext}` | Normal map |
| `texture_metallic.{ext}` | Metallic map |
| `texture_smoothness.{ext}` | Smoothness map |
| `texture_edge.{ext}` | Edge detection map |
| `texture_ao.{ext}` | Ambient occlusion map |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — files generated |
| `1` | Generic error |
| `2` | Input file not found |
| `3` | Unsupported input format |
| `4` | GPU error (no adapter found) |
| `5` | I/O error (permissions, disk full, etc.) |
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
materialize textures/brick_wall.png -p stone -o ./materials/brick/
```

### Batch processing

```bash
# Parallel batch with xargs
ls textures/*.png | xargs -P 4 -I {} materialize {} -o ./pbr/

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

## Getting Help

- **Bugs & features** — [Open an issue](https://github.com/maikramer/Materialize-CLI/issues)
- **Questions** — [GitHub Discussions](https://github.com/maikramer/Materialize-CLI/discussions)
- **Contributing** — See [CONTRIBUTING.md](CONTRIBUTING.md)
