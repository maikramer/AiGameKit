# GameAssets

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for **batched prompts and assets** aligned with your game’s style and concept. Combines a YAML profile (`game.yaml`), a CSV manifest, and style presets, and orchestrates **`text2d`** or **`texture2d`** (seamless textures via API), optionally **`text2sound`** (per-row audio), **`text3d`** (geometry only), **`paint3d`** (Hunyuan3D-Paint 2.1 — texture + PBR in the GLB when `text3d.texture` is enabled), and **Materialize** only for **PBR maps from a diffuse image** in the Texture2D flow (`texture2d.materialize`).

## Requirements

- Python 3.10+
- Commands on `PATH` per workflow (install packages in their environments) or environment variables:
  - `TEXT2D_BIN` — `text2d` executable ([Text2D](../Text2D)) when using **FLUX** 2D generation (`image_source: text2d` or per-row `image_source`)
  - `TEXTURE2D_BIN` — `texture2d` executable ([Texture2D](../Texture2D)) when using **seamless textures** (`image_source: texture2d` or CSV column)
  - `TEXT3D_BIN` — `text3d` executable ([Text3D](../Text3D)) with `--with-3d` (shape only)
  - `PAINT3D_BIN` — `paint3d` executable ([Paint3D](../Paint3D)) when `game.yaml` has **`text3d.texture: true`** (batch calls `paint3d texture` after shape; GLB output is already PBR from Paint 2.1)
  - `TEXT2SOUND_BIN` — `text2sound` executable ([Text2Sound](../Text2Sound)) when CSV rows have **`generate_audio=true`** (and you do not use `--skip-audio`)
  - `MATERIALIZE_BIN` — optional; **PBR maps from diffuse** when using Texture2D + `texture2d.materialize` (see [Materialize](../Materialize) and [Text3D/docs/PBR_MATERIALIZE.md](../Text3D/docs/PBR_MATERIALIZE.md))
  - `PART3D_BIN` — `part3d` executable ([Part3D](../Part3D)) with **`--with-parts`** and CSV column **`generate_parts=true`** (semantic decomposition after Text3D GLB)

## Debug / lab tools

Visual GLB debugging (screenshots, inspect, compare, bundle) lives in **[GameDevLab](../GameDevLab)** (`gamedev-lab debug …`), not in `gameassets`.

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh gameassets
```

General guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development

The project keeps a local **venv** in `GameAssets/.venv`, like Text2D and Text3D.

```bash
cd GameDev/GameAssets
chmod +x scripts/setup.sh activate.sh
./scripts/setup.sh
source .venv/bin/activate
gameassets --help
```

Script options:

| Option | Effect |
|--------|--------|
| *(default)* | Creates `.venv` if missing; `pip install -e .` |
| `--recreate` | Deletes and recreates `.venv` |
| `--dev` | Also installs dev extras (`pytest` via `pip install -e ".[dev]"`) |

**Activate** in each terminal:

```bash
source /path/to/GameDev/GameAssets/.venv/bin/activate
```

`activate.sh` follows the Text2D pattern: runs a command with the venv active, e.g.:

```bash
./activate.sh gameassets prompts --profile game.yaml --manifest manifest.csv
```

**Dependencies:** listed in [`config/requirements.txt`](config/requirements.txt) (installed by `setup.py` on `pip install -e .`). Development: [`config/requirements-dev.txt`](config/requirements-dev.txt) or `./scripts/setup.sh --dev`.

## Three-step flow

| Subcommand | Description |
|------------|-------------|
| `gameassets init` | Creates `game.yaml` and `manifest.csv` in a folder |
| `gameassets prompts` | Preview prompts without generating images |
| `gameassets batch` | Batch-generate images (and optionally 3D/audio) |
| `gameassets info` | Show config, detected binaries, environment |
| `gameassets skill install` | Install Cursor Agent Skill in the project |

### 1. Initialize

```bash
gameassets init --path ./my_game
cd my_game
```

Creates `game.yaml` (profile) and `manifest.csv` (asset list).

### 2. Review prompts (no GPU)

```bash
gameassets prompts --profile game.yaml --manifest manifest.csv
```

Or write JSONL:

```bash
gameassets prompts -o prompts.jsonl --profile game.yaml --manifest manifest.csv
```

### 3. Generate images (and optionally 3D)

**2D only:**

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

**2D + 3D** where `generate_3d=true` in CSV:

```bash
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d
```

**Custom preset** (key only in your `presets-local.yaml`, not in `data/presets.yaml`): you must pass **`--presets-local path.yaml`**, otherwise the command fails with unknown preset.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv --with-3d \
  --presets-local presets-local.yaml --log run.jsonl
```

- Without `--with-3d`, **`text3d` never runs**, even with `generate_3d=true` in the column (warning only).
- With **`--with-3d`** and **`--with-rig`**, rows with **`generate_rig=true`** (and successful Text3D GLB) call **Rigging3D**; rigged GLB appears in the log as **`rig_mesh_path`** (suffix configurable via `rigging3d.output_suffix` in `game.yaml`). Requires **`RIGGING3D_BIN`** or `rigging3d` on `PATH`.
- With **`--with-3d`** and **`--with-parts`**, rows with **`generate_parts=true`** call **Part3D** (`part3d decompose`) on the Text3D GLB **before** rig: outputs **`parts_mesh_path`** (multi-part scene) and **`segmented_mesh_path`** (per-part colors), alongside the main GLB; options under **`part3d`** in `game.yaml`. Requires **`PART3D_BIN`** or `part3d` on `PATH`.
- `--dry-run` prints commands without executing.
- `--fail-fast` stops on first error (default: continue).
- `--log batch-log.jsonl` appends one JSON per processed row, including **`timings_sec`** (wall-clock seconds per subprocess when applicable), e.g. `image_text2d` or `image_texture2d`, `materialize_diffuse`, `text2sound` (when `generate_audio`), `text3d` (single step), or `text3d_shape` / `paint3d_texture` (with `phased_batch` and `text3d.texture`). Records include **`audio_path`** / **`audio_error`** when applicable. **Texture2D** rows include **`texture2d_api`: true** (HF API cost is not computed by GameAssets).
- **Exclusive lock:** `.gameassets_batch.lock` (`fcntl`) in the manifest folder **prevents two batches in the same folder** — avoids VRAM contention between parallel `text2d`/`text3d`. If the PID in the lock no longer exists, the lock is reclaimed. `--skip-batch-lock` disables (advanced).
- **VRAM:** before run, if `nvidia-smi` exists and free VRAM is below ~1.8 GiB, a warning is shown. `--skip-gpu-preflight` disables the warning.
- **CUDA:** `text2d`/`text3d` subprocesses get `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` if unset (reduces fragmentation failures).

### Text2Sound (`generate_audio`)

- Optional CSV column **`generate_audio`** (`true`/`false`): when `true`, after 2D image (phase 1) runs **Text2Sound** (phase 1b) before Text3D.
- Profile: `audio_subdir` (default `audio`) and optional **`text2sound`** block (duration, steps, `wav`/`flac`/`ogg`, etc.) — see [Text2Sound](../Text2Sound).
- **`--skip-audio`:** ignores the column and does not call `text2sound`.
- **`prompts`** includes `prompt_audio` and `generate_audio` in JSONL / preview.

### Text2D vs Texture2D (`image_source`)

In **`game.yaml`**, **`image_source`** picks the default image tool:

| Value | Tool | Notes |
|-------|------|--------|
| `text2d` (default) | FLUX Klein — general images, 3D reference | Uses local VRAM; `text2d` block in YAML |
| `texture2d` | [Texture2D](../Texture2D) — **seamless** textures (HF Inference API) | Low local VRAM; `texture2d` block in YAML (resolution, `materialize` for separate PBR files, etc.) |

**Per CSV row:** optional **`image_source`** (`text2d` or `texture2d`) overrides the profile default for that row (mix FLUX props and Texture2D tiles in one manifest).

Use **`TEXTURE2D_BIN`** if `texture2d` is not on `PATH`.

### PBR in GLB (Paint3D 2.1)

With **`text3d`** in `game.yaml` and **`texture: true`**, the batch runs **`paint3d texture`** on the shape GLB (and reference image). **Hunyuan3D-Paint 2.1** writes a **PBR-ready GLB**; the **Materialize CLI is not used** on 3D meshes in this flow.

Minimal example (with `--with-3d` and `generate_3d=true` rows):

```yaml
text3d:
  preset: fast
  texture: true
```

Context and Texture2D + Materialize: [Text3D/docs/PBR_MATERIALIZE.md](../Text3D/docs/PBR_MATERIALIZE.md).

## Profile (`game.yaml`)

Main fields:

| Field | Description |
|-------|-------------|
| `title`, `genre`, `tone` | Profile metadata; **title does not go to the image prompt** (avoids text/logo in PNG). Genre and tone set mood |
| `style_preset` | Key in `src/gameassets/data/presets.yaml` (`lowpoly`, `pixel_art`, …) |
| `negative_keywords` | Extra restrictions (“Avoid: …”) |
| `output_dir` | Output root (default **`.`** → `./images/` and `./meshes/` without extra `outputs/`) |
| `path_layout` | `split` (default) or `flat` — see below |
| `images_subdir` / `meshes_subdir` | Used with `split`: subfolders for PNG/JPG and GLB |
| `image_ext` | `png` or `jpg` |
| `seed_base` | Optional; seeds derived by `id` for reproducibility |
| `image_source` | `text2d` (default), `texture2d`, or `skymap2d` — default image tool (overridable per CSV column) |
| `text2d` | Optional block: `low_vram`, `cpu`, `width`, `height` |
| `texture2d` | Optional block when using Texture2D (global or CSV `texture2d` lines): resolution, `steps`, `guidance_scale`, `preset`, … and **PBR from diffuse:** `materialize`, `materialize_maps_subdir`, `materialize_bin`, `materialize_format`, etc. |
| `text3d` | Optional block: `preset`, `low_vram`, `texture` (omitted = **`true`**), `steps` / `octree_resolution` / `num_chunks` (mutually exclusive with `preset` in practice), `no_mesh_repair`, `mesh_smooth`, `mc_level`, `phased_batch`, `allow_shared_gpu`, `gpu_kill_others`, `full_gpu`, `model_subfolder` |
| `rigging3d` | Optional block (rig after Text3D): `output_suffix` (e.g. `_rigged`), `root` (Rigging3D package code), `python` (interpreter). Used with `batch --with-rig` and `generate_rig=true` rows |
| `part3d` | Optional block (Part3D after Text3D, before rig): `steps`, `octree_resolution`, `num_chunks`, `segment_only`, `no_cpu_offload`, `verbose`, `parts_suffix`, `segmented_suffix`. Used with `batch --with-parts` and `generate_parts=true` rows |

### Hunyuan3D and quality

With `text3d.low_vram: true` and CUDA GPU, **Text3D** sends Hunyuan3D shape to **CPU** (avoids OOM on ~6 GB), but **shape quality** often degrades badly (blocky meshes). For serious game assets use **`low_vram: false`** with `preset: balanced` or `fast` on GPU and close other VRAM apps (e.g. Godot editor).

### Folder layout (`path_layout`)

- **`split`** — `output_dir/images_subdir/<id>.png` and `output_dir/meshes_subdir/<id>.glb`. `id` may include subpaths (e.g. `Props/crate_01`).
- **`flat`** — `output_dir/<id dir>/<name>.png` and same dir for `<name>.glb`. E.g. `id` = `Collectibles/core` → `output_dir/Collectibles/core.png` and `Collectibles/core.glb`. Good for **one folder per category** in Godot without separate `images/` and `meshes/` branches.

You can create `presets.local.yaml` next to the profile and pass `--presets-local presets.local.yaml` to merge custom presets.

## Manifest (`manifest.csv`)

Headers: **`id`**, **`idea`** (required); optional: **`kind`** (`prop`, `character`, `environment`), **`generate_3d`**, **`generate_audio`**, **`generate_rig`** (`true`/`false`/… — rig GLB after Text3D, with `batch --with-rig`), **`generate_parts`** (`true`/`false`/… — Part3D decomposition after Text3D, with `batch --with-parts`), **`image_source`** (`text2d` \| `texture2d` \| `skymap2d`) to override `game.yaml` `image_source` for that row. With `path_layout: flat`, use `id` with slashes, e.g. `Crystals/shard_blue`, to write files under `Crystals/`.

## Layout

```
GameAssets/
├── src/gameassets/
│   ├── cli.py             # Click CLI (init, prompts, batch, info)
│   ├── profile.py         # game.yaml parsing
│   ├── manifest.py        # manifest.csv parsing
│   ├── prompt_builder.py  # Prompts from profile + preset
│   ├── runner.py          # Subprocesses (text2d, text3d, etc.)
│   ├── presets.py         # YAML preset loading
│   ├── templates.py       # Prompt templates
│   ├── batch_guard.py     # Exclusive lock + VRAM preflight
│   └── data/presets.yaml  # Bundled presets
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   └── setup.sh           # Venv + deps setup
└── tests/
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `TEXT2D_BIN` | Path to `text2d` (if not on `PATH`) |
| `TEXTURE2D_BIN` | Path to `texture2d` |
| `TEXT3D_BIN` | Path to `text3d` |
| `PAINT3D_BIN` | `paint3d` when profile has `text3d.texture` |
| `TEXT2SOUND_BIN` | Path to `text2sound` |
| `MATERIALIZE_BIN` | Path to `materialize` (only when the profile uses Texture2D + `texture2d.materialize`) |
| `RIGGING3D_BIN` | Path to `rigging3d` (or `python -m rigging3d`) with `batch --with-rig` |
| `PART3D_BIN` | Path to `part3d` (or `python -m part3d`) with `batch --with-parts` |
| `PYTORCH_CUDA_ALLOC_CONF` | Auto-set to `expandable_segments:True` if empty (reduces CUDA fragmentation) |

## License

- **Code:** MIT (aligned with the rest of the monorepo).
- **Invoked models** (`text2d`, `texture2d`, `skymap2d`, `text2sound`, `text3d`, `part3d`, `rigging3d`): each tool downloads or uses weights under its own license (FLUX, Tencent Hunyuan, Stability Audio, UniRig, etc.). **Do not** confuse the MIT `gameassets` code with checkpoint licenses. Table and notes: [monorepo README — Licenses](../README.md).
