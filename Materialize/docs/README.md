# Materialize CLI — Documentation

This folder contains the main documentation for Materialize CLI (in English). For a Portuguese overview, see [README_PT.md](README_PT.md).

## Overview

Materialize CLI is a command-line tool that converts a diffuse/albedo image into six PBR (Physically Based Rendering) maps using GPU compute shaders via [wgpu](https://wgpu.rs/). Based on the original [Materialize](https://github.com/BoundingBoxSoftware/Materialize) (Unity/Windows), this CLI version is:

- **Minimal** — No GUI, no Unity; command-line only
- **Fast** — GPU processing via compute shaders
- **Cross-platform** — Linux, Windows, macOS via wgpu
- **Straightforward** — One command, six output maps

## Installation

### AiGameKit monorepo

From the **AiGameKit** repository root (parent of this `Materialize/` folder):

```bash
cd /path/to/AiGameKit
./install.sh materialize
```

See [docs/INSTALLING.md](../../docs/INSTALLING.md).

### Standalone clone (recommended for upstream-only checkouts)

Requirements: Python 3 (for the installer), Rust/cargo (to build).

```bash
git clone https://github.com/maikramer/Materialize-CLI.git
cd Materialize-CLI

# Linux/macOS
./install.sh

# Or run the Python installer directly
python3 installer/installer.py install

# Uninstall
./install.sh uninstall
# or
python3 installer/installer.py uninstall
```

The installer runs `cargo build --release` and copies the binary to `~/.local/bin/materialize`. Ensure `~/.local/bin` is on your `PATH`.

**Windows:** use `install.ps1` (PowerShell) or `install.bat` (CMD).

### Manual build (Cargo)

```bash
cargo build --release
cargo install --path .
```

## Basic usage

```bash
# Generate all maps from a texture
materialize texture.png

# Output files:
# texture_height.png, texture_normal.png, texture_metallic.png,
# texture_smoothness.png, texture_edge.png, texture_ao.png

# Specify output directory
materialize texture.png -o ./output/

# Choose output format
materialize texture.png --format exr
```

## Documentation index

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System structure and components |
| [Features](features.md) | Capabilities and behavior |
| [CLI API](cli-api.md) | Command-line reference, options, env vars, shell completion |
| [Algorithms](algorithms.md) | Processing algorithms (height, normal, metallic, etc.) |
| [Shaders](shaders.md) | WGSL compute shaders |
| [Roadmap](roadmap.md) | Future plans and ideas |

## Requirements

- Rust 1.75+
- GPU with Vulkan (Linux), Metal (macOS), or DirectX 12 (Windows) support; up-to-date drivers

## License

MIT. Based on the original Materialize by Bounding Box Software.
