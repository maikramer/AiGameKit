# GameDevLab — Debug, Benchmark & Performance

> CLI toolkit for the GameDev monorepo: 3D visual debugging, GLB validation, GPU benchmarking, performance analytics, CPU profiling, and mesh quality inspection. Replaces the legacy `gameassets debug` (removed from GameAssets).

## Overview

GameDevLab is the unified debugging and quality-assurance tool for the entire GameDev pipeline. It provides:

- **`check`** — Declarative GLB validation against YAML/JSON rules (CI-ready, exit 0/1)
- **`debug`** — Visual debugging: multi-angle screenshots, metadata inspection, side-by-side comparison, agent bundles, and rig inspection
- **`bench`** — GPU benchmarks for Part3D, Paint3D quantization, SDNQ sweeps, full pipeline optimization, and batch sweeps
- **`perf`** — Performance analytics over an SQLite database (runs, VRAM analysis, config recommendations)
- **`profile`** — CPU profiling via cProfile
- **`mesh`** — Mesh quality inspection (topology, geometry, artifacts), visual QA, and topological diff

Requires `gamedev-shared` as a dependency. Rendering commands (`debug screenshot`, `debug compare`, `debug bundle`, `debug inspect-rig`, `mesh qa`, `mesh render-views`) require `animator3d` on `PATH` or `ANIMATOR3D_BIN` set.

## Installation

From the monorepo root:

```bash
cd Shared && pip install -e .
cd GameDevLab && pip install -e .
```

For GPU benchmarks (Part3D, Paint3D, SDNQ, Quanto):

```bash
cd GameDevLab && pip install -e ".[bench]"
```

Without `pip install`, using `PYTHONPATH`:

```bash
export PYTHONPATH="/path/to/GameDevLab/src:/path/to/Shared/src"
python -m gamedev_lab --help
```

## Commands

Entry point: `gamedev-lab` or `python -m gamedev_lab`

### check — GLB Validation

Validate GLB files against declarative YAML/JSON rules. CI-ready (exits 0 on pass, 1 on failure).

```bash
gamedev-lab check glb modelo.glb rules.yaml
gamedev-lab check glb modelo.glb rules.yaml --json-out report.json --quiet
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json-out` | path | None | Write JSON report to file (stdout if omitted with `--quiet`) |
| `--quiet`, `-q` | flag | false | Only exit code; errors on stderr |

Rule files support vertex/face limits, `world_bounds`, required bones, and more. See [`examples/glb_rules.example.yaml`](examples/glb_rules.example.yaml) and [`examples/glb_rules_permissive.yaml`](examples/glb_rules_permissive.yaml).

---

### debug — Visual Debug Tools

All rendering commands delegate to `animator3d` (requires `ANIMATOR3D_BIN` or `animator3d` on `PATH`).

#### `debug screenshot GLB`

Generate multi-angle screenshots using the native bpy renderer.

```bash
gamedev-lab debug screenshot modelo.glb -o ./screenshots
gamedev-lab debug screenshot modelo.glb -o ./frames --frame-list 1,36,72
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | `{stem}_debug` | Output directory |
| `--views` | str | `front,three_quarter,right,back` | Comma-separated view names |
| `-r, --resolution` | int | `512` | Render resolution in pixels |
| `--show-bones` | flag | false | Show armature wireframe |
| `--frame` | int | None | Single frame for all views |
| `--frame-list` | str | None | Comma-separated frames (e.g. `1,36,72`) — files named `view_fNNNN.png` |
| `--engine` | str | `workbench` | Render engine: `workbench` or `eevee` |
| `--ortho` | flag | false | Orthographic camera |
| `--no-transparent-film` | flag | false | Opaque background |

#### `debug bundle GLB`

Full agent bundle: inspect JSON + multi-angle screenshots + `bundle.json`. The bundle includes extra views (`low_front`, `worm`) by default.

```bash
gamedev-lab debug bundle modelo.glb -o ./out_bundle
gamedev-lab debug bundle modelo.glb -o ./out_bundle --include-rig --rig-weights spine
```

All screenshot flags apply, plus:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--include-rig` | flag | false | Generate `rig/` subfolder with inspect-rig (bones + optional heatmap) |
| `--rig-weights` | str | None | Bone name for heatmap (requires `--include-rig`) |

The `bundle.json` tracks `tool: gamedev_lab.debug.bundle`, version, input paths, inspect data, screenshots, world bounds, and optional rig report.

#### `debug inspect GLB`

Dump mesh/armature/animation metadata as JSON (native bpy, no rendering).

```bash
gamedev-lab debug inspect modelo.glb
gamedev-lab debug inspect modelo.glb -o metadata.json
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | None | Save JSON to file (stdout if omitted) |

#### `debug inspect-rig GLB`

Rig inspection with bone wireframe and optional weight heatmap. Delegates to `animator3d inspect-rig`.

```bash
gamedev-lab debug inspect-rig modelo.glb -o ./rig_debug --show-weights spine
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | `{stem}_rig_debug` | Output directory |
| `--show-weights` | str | None | Bone name for weight heatmap |
| `--views` | str | `front,three_quarter,right,back` | Comma-separated view names |
| `-r, --resolution` | int | `512` | Render resolution |
| `--engine` | str | `workbench` | Render engine: `workbench` or `eevee` |
| `--ortho` | flag | false | Orthographic camera |
| `--no-transparent-film` | flag | false | Opaque background |

#### `debug compare A B`

Side-by-side visual + structural comparison of two GLB models.

```bash
gamedev-lab debug compare before.glb after.glb -o ./comparison --image-metrics
gamedev-lab debug compare before.glb after.glb -o ./ci --image-metrics --fail-below-ssim 0.85
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | `{a}_vs_{b}` | Output directory |
| `--views` | str | `front,three_quarter` | Comma-separated views to compare |
| `-r, --resolution` | int | `512` | Render resolution |
| `--with-inspect` | flag | false | Include full inspect JSON per model |
| `--struct-diff` / `--no-struct-diff` | flag | `true` | Compute `inspect_diff` (vertex/face deltas per view) |
| `--image-metrics` | flag | false | Compute MAE, RMSE, SSIM per view (numpy) |
| `--fail-below-ssim` | float | None | Exit 1 if any view's SSIM falls below threshold (requires `--image-metrics`) |
| `--engine` | str | `workbench` | Render engine: `workbench` or `eevee` |
| `--ortho` | flag | false | Orthographic camera |

Output: side-by-side PNGs per view (`compare_{view}.png`) + `diff_report.json` with `inspect_diff` section and optional `image_metrics`.

---

### bench — GPU Benchmarks

GPU benchmarks require the `[bench]` extra: `pip install -e ".[bench]"`.

#### `bench part3d`

Part3D decomposition benchmarks with VRAM monitoring (quantization modes: none / quanto / SDNQ).

```bash
gamedev-lab bench part3d --mesh meshes/foo.glb --modo sdnq-uint8 --project-dir ./myproject
gamedev-lab bench part3d --modo sweep   # run all configs
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mesh` | path | `meshes/boa_mesa/tigela_ceramica.glb` | GLB file (relative to `--project-dir`) |
| `--modo` | str | `baseline-fp16` | Config name or `sweep` for all |
| `-o, --output-dir` | path | `test_part3d_results` | Results directory |
| `--project-dir` | path | `.` | Base directory for relative paths |

#### `bench paint-vram`

Paint3D quantization sweet-spot search with VRAM monitoring.

```bash
gamedev-lab bench paint-vram --image reference.png --target-vram-mb 5500
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--image` | path | None | Reference image for apply_hunyuan_paint |
| `--target-vram-mb` | float | `5500` | Target VRAM budget in MB |
| `--output-json` | path | `quantization_vram_results.json` | Output JSON file |
| `--project-dir` | path | `.` | Base for relative paths |

#### `bench pre-quantize`

Pre-quantize SDNQ DiT (Part3D) and/or UNet (Paint3D) models.

```bash
gamedev-lab bench pre-quantize --modelo paint3d
gamedev-lab bench pre-quantize --modelo todos --dry-run
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--modelo` | str | `todos` | Target: `part3d`, `paint3d`, or `todos` |
| `--dry-run` | flag | false | Check SDNQ availability without quantizing |

#### `bench sdnq-sweep`

SDNQ configuration sweep for Paint3D — tests TinyVAE, attention slicing, and 4/8-bit quantization.

```bash
gamedev-lab bench sdnq-sweep --mesh modelo.glb --image ref.png \
  --target-vram-mb 5500 -o sweep_results
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mesh` | path | **required** | Input mesh GLB |
| `--image` | path | **required** | Reference image for texturing |
| `-o, --output-dir` | path | `sdnq_sweep_results` | Output directory |
| `--target-vram-mb` | float | `5500` | Maximum VRAM target in MB |
| `--project-dir` | path | `.` | Base directory for relative paths |

#### `bench pipeline-opt`

Optimize the full Part3D + Paint3D pipeline. Automatically iterates configs with fallback on OOM.

```bash
gamedev-lab bench pipeline-opt --mesh input.glb --image ref.png \
  --target-vram-mb 6000 --steps 50 --octree 256
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mesh` | path | **required** | Input mesh GLB |
| `--image` | path | **required** | Reference image for texturing |
| `-o, --output-dir` | path | `pipeline_opt_results` | Output directory |
| `--target-vram-mb` | float | `5500` | Maximum VRAM target in MB |
| `--steps` | int | `50` | Part3D decomposition steps |
| `--octree` | int | `256` | Octree resolution for Part3D |
| `--project-dir` | path | `.` | Base directory for relative paths |

The optimizer tests stable Paint3D configs first (`paint3d-qint8-*`), then experimental SDNQ configs, monitors VRAM in real time, and falls back to lighter configs on OOM.

#### `bench batch`

GameAssets batch configuration sweep with quantization profiles.

```bash
gamedev-lab bench batch --mode sweep --project-dir ./myproject --manifest manifest.csv
gamedev-lab bench batch --mode test --config baseline-fp16
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mode` | str | `dry-run` | Mode: `sweep`, `test`, or `dry-run` |
| `--config` | str | None | Config name (for `test` mode) |
| `-o, --output-dir` | path | `test_results` | Results directory |
| `--project-dir` | path | `.` | Example directory (cwd for tests) |
| `--manifest` | path | `project-dir/manifest_3obj.csv` | Manifest CSV file |

---

### perf — Performance Analysis

Performance analytics backed by an SQLite perf database (stored in the monorepo data directory).

#### `perf list`

List recent performance runs.

```bash
gamedev-lab perf list --tool text2d -n 10
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tool`, `-t` | str | None | Filter by tool name (e.g. `text2d`, `text3d`) |
| `--limit`, `-n` | int | `20` | Number of runs to display |
| `--db` | path | None | Path to `perf.db` |

#### `perf show RUN_ID`

Show detailed spans for a specific run.

```bash
gamedev-lab perf show 42
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--db` | path | None | Path to `perf.db` |

#### `perf summary`

Aggregated performance summary grouped by tool and quantization mode.

```bash
gamedev-lab perf summary --tool text3d --gpu 4090 --days 30
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tool`, `-t` | str | None | Filter by tool name |
| `--gpu` | str | None | Filter by GPU name (substring match) |
| `--quant` | str | None | Filter by quantization mode |
| `--days` | int | `30` | Time window in days |
| `--db` | path | None | Path to `perf.db` |

#### `perf vram`

VRAM usage analysis by tool, quantization, and span.

```bash
gamedev-lab perf vram --tool text3d --gpu 4090
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tool`, `-t` | str | None | Filter by tool name |
| `--gpu` | str | None | Filter by GPU name (substring match) |
| `--days` | int | `30` | Time window in days |
| `--db` | path | None | Path to `perf.db` |

#### `perf recommend TOOL`

Recommend the best quantization configuration for a tool given a VRAM budget.

```bash
gamedev-lab perf recommend text2d --vram 8000
gamedev-lab perf recommend text3d --vram 6000 --gpu 4070 --days 90
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `TOOL` | arg | **required** | Tool name (e.g. `text2d`, `text3d`) |
| `--vram` | float | **required** | Available VRAM in MB |
| `--gpu` | str | None | Filter by GPU name (substring match) |
| `--days` | int | `90` | Time window in days |
| `--db` | path | None | Path to `perf.db` |

#### `perf clean`

Delete old performance runs from the database.

```bash
gamedev-lab perf clean --days 30
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--days` | int | `90` | Delete runs older than N days |
| `--db` | path | None | Path to `perf.db` |

---

### profile — CPU Profiling

#### `profile cprofile SCRIPT`

Run a Python script with `cProfile` instrumentation.

```bash
gamedev-lab profile cprofile -o out.prof ./script.py -- --arg1 --arg2
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | `{stem}.prof` | Output `.prof` file |
| `args` | args | None | Extra arguments after `--` (forwarded to script) |

---

### mesh — Mesh Quality

Mesh topology, geometry, and artifact inspection. Rendering commands require `bpy`.

#### `mesh inspect MESH`

Analyze mesh quality: topology stats, geometry measurements, and artifact detection. Outputs a grade (A–F) and pass/fail verdict.

```bash
gamedev-lab mesh inspect modelo.glb
gamedev-lab mesh inspect modelo.glb -v --json-out report.json
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json-out` | path | None | Save report as JSON |
| `--verbose`, `-v` | flag | false | Show detailed Rich tables |

#### `mesh qa MESH`

Full QA pipeline: topology inspect + render views + optional reference image comparison (SSIM).

```bash
gamedev-lab mesh qa modelo.glb -o ./qa_output --reference-image reference.png
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | `{stem}_qa` | Output directory |
| `--reference-image` | path | None | Reference image for visual comparison |
| `--views` | str | `front,three_quarter,right,back,top,low_front` | Comma-separated views |
| `-r, --resolution` | int | `512` | Render resolution |
| `--engine` | str | `workbench` | Render engine: `workbench` or `eevee` |

#### `mesh render-views MESH`

Render multi-angle views of a GLB for visual inspection.

```bash
gamedev-lab mesh render-views modelo.glb -o ./views --engine eevee
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output-dir` | path | `{stem}_views` | Output directory |
| `--views` | str | `front,three_quarter,right,back,top,low_front` | Comma-separated views |
| `-r, --resolution` | int | `512` | Render resolution |
| `--engine` | str | `workbench` | Render engine: `workbench` or `eevee` |

#### `mesh diff A B`

Topological mesh comparison: vertices, faces, edges, holes, UV seams, Euler number, volume, and more.

```bash
gamedev-lab mesh diff before.glb after.glb --json-out diff.json
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json-out` | path | None | Save diff report as JSON |

Output: Rich table with delta analysis + optional JSON with full topology/geometry comparison.

---

## Mesh Comparison Workflow

Use `debug screenshot` and `debug compare` for visual regression testing:

```bash
# 1. Capture baseline screenshots
gamedev-lab debug screenshot before.glb -o baseline/

# 2. Capture new screenshots (after changes)
gamedev-lab debug screenshot after.glb -o after/

# 3. Automated structural + visual comparison
gamedev-lab debug compare before.glb after.glb \
  --image-metrics \
  --fail-below-ssim 0.85 \
  -o ./comparison
```

- `--struct-diff` (on by default) generates an `inspect_diff` section in `diff_report.json` with per-view vertex/face counts.
- `--image-metrics` adds MAE, RMSE, and SSIM scores per view.
- `--fail-below-ssim` exits with code 1 if any view falls below the threshold — useful in CI or pre-commit hooks.

For pure topological comparison (no rendering), use `mesh diff`:

```bash
gamedev-lab mesh diff original.glb remeshed.glb --json-out topo_diff.json
```

## SDNQ Quantization Reference

### Stable Configurations (native Paint3D qint8)

| Configuration | Quantization | TinyVAE | Views | Resolution | VRAM | Status |
|-------------|-------------|---------|-------|-----------|------|--------|
| `paint3d-qint8-balanced` | qint8 native | No | 6 | 384px | Medium | **Stable** |
| `paint3d-qint8-stable` | qint8 native | No | 4 | 256px | Low | **Stable** |

### SDNQ Configurations (experimental)

| Configuration | Bits | TinyVAE | Views | Resolution | VRAM |
|-------------|------|---------|-------|-----------|------|
| `sdnq-uint8-full` | 8 | No | 6 | 512px | High |
| `sdnq-uint8-tiny` | 8 | Yes | 4 | 384px | Medium |
| `sdnq-uint8-minimal` | 8 | Yes | 2 | 256px | Low |
| `sdnq-int4-full` | 4 | No | 6 | 512px | Medium |
| `sdnq-int4-tiny` | 4 | Yes | 4 | 384px | Low |
| `sdnq-int4-minimal` | 4 | Yes | 2 | 256px | Minimal |
| `sdnq-fp8` | 8 (FP8) | No | 6 | 512px | High (RTX 40 series) |

**Compatibility notes:**
- **TinyVAE**: Incompatible with `HunyuanPaintPBR` (requires `latent_dist` not provided by TinyVAE).
- **SDNQ in Paint3D**: The custom UNet `UNet2p5DConditionModel` may behave differently with SDNQ applied.
- **Part3D with SDNQ**: Works correctly with `sdnq-uint8`.

### Optimization Techniques

- **TinyVAE (TAESD)**: Reduces VAE VRAM by ~70% (incompatible with HunyuanPaintPBR).
- **Attention Slicing**: Processes attention in slices to reduce peak VRAM.
- **VAE Tiling**: Processes large images in tiles.
- **torch.compile**: JIT compilation for faster inference (may cause instability).
- **Automatic Fallback**: On OOM, automatically retries with a lighter configuration.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANIMATOR3D_BIN` | Path to `animator3d` binary (required for rendering commands) |
| `GAMEDEV_PROFILE` | Enable profiling when set to `1` |
| `GAMEDEV_PROFILE_LOG` | Profiler log output path |
| `GAMEDEV_ROOT` | Monorepo root directory (auto-detected if omitted) |

## Pipeline Integration

GameDevLab is the debugging and validation layer for the entire GameDev pipeline:

| Use Case | Command |
|----------|---------|
| CI validation | `gamedev-lab check glb model.glb rules.yaml` |
| Visual regression | `gamedev-lab debug compare before.glb after.glb --image-metrics` |
| Agent artifact audit | `gamedev-lab debug bundle model.glb -o ./audit` |
| Mesh quality gate | `gamedev-lab mesh qa model.glb -o ./qa` |
| GPU optimization | `gamedev-lab bench pipeline-opt --mesh ... --image ...` |
| Config recommendation | `gamedev-lab perf recommend text2d --vram 8000` |
| Performance tracking | `gamedev-lab perf summary --tool text3d --days 30` |
| CPU profiling | `gamedev-lab profile cprofile -o out.prof ./script.py` |

**Migration from GameAssets:** The legacy `gameassets debug` has been replaced by `gamedev-lab debug`. The `bundle.json` field now uses `tool: gamedev_lab.debug.bundle`.

## Development

```bash
cd GameDevLab

# Install in editable mode
pip install -e ".[dev]"

# Run tests
pytest tests

# Lint
ruff check .

# Format
ruff format .
```

Requires `gamedev-shared` installed first (see Installation).
