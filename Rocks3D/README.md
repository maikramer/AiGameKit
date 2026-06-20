# Rocks3D — Procedural 3D Rock Generation

CLI for generating procedural 3D rocks as GLB meshes with embedded PBR materials. No AI models or GPU required. Rocks combine FBM simplex displacement with a **convex fracture polytope** (random cutting planes) so they read as angular stone rather than smooth blobs, plus a high-frequency detail octave, light Taubin smoothing, an optional flat base, and a superficial curvature-based weathering pass that rounds exposed edges without flattening the rock.

## Overview

- **Believable geometry** — FBM lumps blended with a random fracture polytope give flat faces and sharp edges; `facet_strength` spans rounded pebbles → angular boulders
- **Five rock types** — `pebble`, `boulder`, plus scenery rocks `spire`, `slab`, `outcrop`
- **Rock formations (rochedos)** — `rocks3d formation` unions several chunks into one mesh, producing the *concave* geometry a heightmap cannot express: overhangs, arches, crevices and balanced stacks. Styles: `stack`, `outcrop`, `cliff`, `arch`, `spire-cluster`
- **Quality presets** — 5 tiers (`fast` through `highest`) that control subdivision and noise octaves (geometry honours the tier, not just erosion)
- **Embedded PBR** — base-color, normal, metallic-roughness and occlusion textures are generated (via [Materialize](../Materialize/), with a procedural fallback) and embedded in the GLB with UVs as a glTF `PBRMaterial`
- **GLB output** — single self-contained file per rock, directly loadable in Three.js, Blender, or any GLTF consumer
- **Reproducible** — full seed control

## Installation

### Monorepo (recommended)

```bash
cd Shared && pip install -e .
cd Rocks3D && pip install -e .
```

Or use the unified installer:

```bash
./install.sh rocks3d
```

### Requirements

- Python 3.13+
- `gamedev-shared` (install Shared first)
- No GPU required

## Quick Start

```bash
# Generate a boulder
rocks3d generate boulder --seed 42 -o rock.glb

# Generate a pebble with high quality
rocks3d generate pebble --seed 123 -o pebble.glb --quality high

# Fast generation for prototyping
rocks3d generate boulder --seed 7 --quality fast -o quick_rock.glb
```

## Commands

Entry point: `rocks3d` or `python -m rocks3d`

### `rocks3d generate TYPE`

Generate a procedural 3D rock mesh.

```bash
rocks3d generate boulder --seed 42 -o rock.glb
rocks3d generate pebble --seed 99 -o pebble.glb --scale 2.0 --no-erosion
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `TYPE` | choice | required | Rock type: `pebble` or `boulder` |
| `-o, --output` | path | auto | Output GLB path |
| `--seed` | int | random | Reproducible seed |
| `--quality` | choice | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--category` | str | None | Asset category for QualityEngine overrides |
| `--scale` | float | `1.0` | Scale factor applied to the final mesh |
| `--erosion/--no-erosion` | flag | erosion | Toggle simulated erosion smoothing |

### `rocks3d batch TYPE`

Batch generate rocks with sequential seeds.

```bash
rocks3d batch boulder -n 10 -o rocks/            # 10 boulders, seeds 0..9
rocks3d batch both -n 5 --quality high -o rocks/ # 5 pebbles + 5 boulders
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `TYPE` | choice | required | `pebble`, `boulder`, or `both` |
| `-n, --count` | int | `5` | Rocks per type |
| `-o, --output-dir` | path | `rocks` | Output directory |
| `--seed` | int | `0` | Starting seed (incremented per rock) |
| `--quality` | choice | `medium` | Quality tier |
| `--scale` | float | `1.0` | Scale factor |
| `--erosion/--no-erosion` | flag | erosion | Toggle erosion |

Files are written as `<output-dir>/<type>_<seed>.glb`.

### `rocks3d formation STYLE`

Generate a scenery rock **formation** (rochedo) by unioning several angular
chunks. A single rock is roughly convex — exactly what heightmap terrain already
gives you. Formations interpenetrate multiple chunks and boolean-union them, so
the result is *non-convex*: overhangs, arches, crevices and balanced stacks that
a heightmap cannot represent. Drop them on terrain to add cliffs and caves.

```bash
rocks3d formation arch --seed 7 -o arch.glb
rocks3d formation outcrop -n 6 -o formations/ --quality high   # batch into a dir
rocks3d formation cliff --chunks 6 --scale 3 -o cliff.glb
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `STYLE` | choice | required | `stack`, `outcrop`, `cliff`, `arch`, or `spire-cluster` |
| `-o, --output` | path | auto | GLB file (when `-n 1`) or directory (when `-n > 1`) |
| `--seed` | int | random | Reproducible seed (incremented per item when `-n > 1`) |
| `-n, --count` | int | `1` | How many formations to generate |
| `--chunks` | int | per-style | Override the number of chunks unioned |
| `--quality` | choice | `medium` | Quality tier (chunk subdivision) |
| `--scale` | float | `1.0` | Scale factor applied to the final mesh |
| `--bake/--no-bake` | flag | auto | Seamless bpy bake (else trimesh fallback) |

Each formation is recentred on XZ with its base at `y = 0`, ready to place.

| Style | Shape | Concave feature |
|-------|-------|-----------------|
| `stack` | boulders piled into a tower | overhangs at the joints |
| `outcrop` | boulders jammed at ground level | crevices, ledges |
| `cliff` | row of tall tilted slabs | vertical rock wall / cliff face |
| `arch` | two pillars bridged by a lintel | a real hole underneath |
| `spire-cluster` | cluster of hoodoo spires | gaps between spires |

## Rock Types

| Type | Subdiv | Radius | Octaves | Freq | Amp | Facet str. | Planes | Erosion | Typical Verts | Use Case |
|------|--------|--------|---------|------|-----|------------|--------|---------|---------------|----------|
| `pebble` | 2 | 0.1 | 4 | 2.5 | 0.20 | 0.38 | 10 | 0 | ~162 | Ground scatter, debris, small props |
| `boulder` | 4 | 1.0 | 5 | 2.2 | 0.18 | 0.55 | 15 | 1 | ~2.5k | Landscape features, obstacles, cover |

`facet_strength` blends the smooth displaced sphere with a convex fracture polytope (`planes` cutting planes): higher = more angular. Both types use non-uniform scale and a flattened base so they sit on terrain. Vertex counts vary with quality tier (and rise further after xatlas UV unwrapping splits seam vertices).

## Quality Presets

The `--quality` flag adjusts subdivision, noise octaves, and erosion passes relative to the base rock type. CLI parameters always win over quality defaults.

```bash
rocks3d generate boulder --quality fast        # low poly, no erosion
rocks3d generate boulder --quality highest     # maximum detail
```

| Tier | Subdivisions (delta) | Octaves (delta) | Erosion Passes | Description |
|------|---------------------|-----------------|----------------|-------------|
| `fast` | -1 | -1 | 0 | Minimum viable quality, no erosion |
| `low` | 0 | 0 | (base) | Basic quality |
| `medium` | 0 | 0 | (base) | Standard (default) |
| `high` | +1 | +1 | (base) | Higher polygon count and detail |
| `highest` | +2 | +2 | +1 | Maximum detail, extra erosion |

Negative values are subtracted from the base preset. For example, a `fast` boulder (base subdivisions=4) becomes subdivisions=3.

## PBR Textures

Two texturing backends, selected automatically (override with `--bake/--no-bake`):

**bpy bake (default when Blender's `bpy` is importable — it ships via `gamedev-shared`).** Builds a procedural material driven by **object-space** coordinates (3D-coherent) plus geometry pointiness for cavities, then bakes albedo, normal, roughness and AO to UV images with a bake margin. Because the source signal is coherent in 3D and the margin floods UV-island gutters, the textures are **seamless**. The GLB is exported with smooth vertex normals and MikkTSpace **tangents**, so the normal map renders without seams too.

**trimesh fallback.** Embeds a glTF `PBRMaterial` with a procedural albedo (cavity darkening), plus normal/roughness/AO via [Materialize](../Materialize/) (or a procedural fallback). UVs come from xatlas (boulders) / spherical projection (pebbles); smooth normals are carried through the xatlas vertex-split and exported so shading has no seams, though 2D textures can still seam across UV islands — prefer the bpy backend for final assets.

## GameAssets Integration

Use `rocks3d` within the [GameAssets](../GameAssets/) batch pipeline by adding it to `manifest.csv` and configuring the `rocks3d` block in `game.yaml`.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ROCKS3D_BIN` | Override `rocks3d` binary path (used by GameAssets) |

## Development

```bash
cd Rocks3D && pip install -e ".[dev]"
make test-rocks3d
ruff check .
ruff format .
```

## License

MIT — see [LICENSE](LICENSE).
