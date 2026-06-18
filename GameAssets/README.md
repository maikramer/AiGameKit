# GameAssets — Batch Asset Generation Pipeline

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

Orchestrator for the entire GameDev pipeline. Coordinates **Text2D**, **Texture2D**, **Skymap2D**, **Text2Sound**, **Text3D**, **Paint3D**, **Part3D**, **Rigging3D**, **Animator3D**, **Materialize**, and **Terrain3D** to generate complete game assets from a YAML manifest and profile.

Reads `game.yaml` (style + tool configuration) and `manifest.yaml` (asset list), then runs each sub-tool in the correct order, tracking progress, managing VRAM, and producing a structured output directory ready for handoff to Vite / VibeGame.

## Overview

GameAssets is the central hub of the [GameDev monorepo](../). It does **not** generate images or meshes itself — it delegates to specialized packages and manages the workflow:

- **2D generation:** Text2D (FLUX, local GPU) or Texture2D (seamless textures, HF API) or Skymap2D (equirectangular 360° sky)
- **3D shape:** Text3D (Hunyuan3D-2.1, image→geometry)
- **3D texturing:** Paint3D (Hunyuan3D-Paint 2.1, PBR-ready GLB) with optional quick paint (solid / perlin)
- **Semantic parts:** Part3D (decompose mesh into semantic segments)
- **Auto-rigging:** Rigging3D (UniRig, GLB → rigged GLB)
- **Animation:** Animator3D (`game-pack`, rigged GLB → animated GLB with clips)
- **Audio:** Text2Sound (Stable Audio Open, per-row SFX / BGM)
- **PBR maps:** Materialize (diffuse → normal / metallic / roughness / AO, only for Texture2D flow)
- **Terrain:** Terrain3D (AI terrain generation via diffusion, from `dream` command)
- **LOD / collision:** Text3D `lod` / `collision` sub-commands
- **Quality presets:** QualityEngine integration (`fast` | `low` | `medium` | `high` | `highest`)
- **Idea-to-game:** `dream` command — LLM plans assets + scene, batch generates everything, scaffolds a playable Vite project

## Pipeline Flow

```
Manifest + game.yaml
        │
        ▼
   ┌─────────┐     ┌──────────┐     ┌──────────┐
   │ Text2D  │ or  │Texture2D │ or  │Skymap2D  │ → Reference Images
   └────┬────┘     └────┬─────┘     └──────────┘
        │               │
        ▼               ▼
   ┌──────────┐    ┌───────────┐
   │ Text3D   │    │ Text2Sound│ → Audio
   └────┬─────┘    └───────────┘
        │
        ▼
   ┌──────────┐
   │ Paint3D  │ → Textured PBR-ready GLB
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │  Part3D  │ → Decomposed Parts (optional)
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │Rigging3D │ → Rigged GLB
   └────┬─────┘
        │
        ▼
   ┌──────────┐
   │Animator3D│ → Animated GLB
   └──────────┘
```

**Execution order within `batch`:**

1. **Text2D / Texture2D** — Generate 2D reference images per row
2. **Materialize** (optional) — PBR maps from diffuse (Texture2D flow only)
3. **Text2Sound** — Audio for rows with `audio` in pipeline
4. **Text3D** — Shape generation (image→3D)
5. **Paint3D** — Texture + PBR (Hunyuan3D-Paint 2.1 or quick paint)
6. **Part3D** — Semantic decomposition (optional)
7. **Rigging3D** — Auto-rigging (optional)
8. **Animator3D** — Animation clips (`game-pack`, optional)
9. **LOD** — Level-of-detail triplet (optional)
10. **Collision** — Convex hull collision mesh (optional)

Pipeline stages (3D, rig, parts, animate, lod, collision) are **auto-detected** from the manifest `pipeline` field and `game.yaml` profile blocks. Use `--no-3d`, `--no-rig`, `--no-parts`, `--no-animate`, `--no-lod`, `--no-collision` to explicitly opt out.

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

```bash
cd Shared && pip install -e .       # Shared dependency first
cd GameAssets && pip install -e .    # GameAssets
gameassets --help
```

Dev extras (pytest, ruff, bpy):

```bash
cd GameAssets && pip install -e ".[dev]"
```

**Requirements:** Python 3.10+, `gamedev-shared`, click, rich, rich-click, textual, Pillow, PyYAML.

## Commands

### `gameassets init`

```bash
gameassets init [--path DIR] [--force]
```

Creates `game.yaml` and `manifest.yaml` template files.

| Flag | Description |
|------|-------------|
| `--path DIR` | Target directory (default: `.`) |
| `--force` | Overwrite existing files |

### `gameassets info`

```bash
gameassets info
```

Shows version, resolved binaries for all tools (Text2D, Texture2D, Skymap2D, Text2Sound, Text3D, Paint3D, Part3D, Rigging3D, Animator3D, Materialize), and free VRAM (via `nvidia-smi`).

### `gameassets prompts`

```bash
gameassets prompts --profile game.yaml --manifest manifest.yaml \
  [--presets-local FILE] [-o prompts.jsonl]
```

Preview / generate final prompts without using GPU. Shows a table with `id`, `category`, `target_faces`, pipeline flags (3D, audio, rig, animate), and prompt preview.

| Flag | Description |
|------|-------------|
| `--profile` | Game profile YAML (default: `game.yaml`) |
| `--manifest` | Asset manifest (default: `manifest`) |
| `--presets-local` | Optional custom presets YAML |
| `-o FILE` | Write prompts as JSONL (one line per asset) |

JSONL entries include: `id`, `prompt`, `prompt_3d_hint`, `prompt_audio`, `generate_3d`, `generate_audio`, `generate_rig`, `generate_animate`, `category`, `target_faces`.

### `gameassets batch`

```bash
gameassets batch --profile game.yaml --manifest manifest.yaml [options]
```

Full pipeline execution. Generates 2D images, 3D meshes, textures, audio, rigging, animation, LODs, and collision meshes according to the manifest.

| Flag | Description |
|------|-------------|
| `--profile` | Game profile YAML (default: `game.yaml`) |
| `--manifest` | Asset manifest (default: `manifest`) |
| `--presets-local FILE` | Custom style presets YAML |
| `--no-3d` | Skip 3D generation entirely |
| `--dry-run` | Show commands without executing |
| `--dry-run-json plan.json` | Save execution plan as JSON |
| `--fail-fast` | Stop on first error (default: continue) |
| `--log run.jsonl` | JSONL log with one record per asset |
| `--skip-batch-lock` | Allow concurrent batches (dangerous — VRAM contention) |
| `--skip-gpu-preflight` | Skip VRAM warning |
| `--skip-text2d` | Skip 2D image generation (use existing PNGs) |
| `--skip-audio` | Skip Text2Sound (ignore `audio` in pipeline) |
| `--no-rig` | Skip rigging even if configured |
| `--no-parts` | Skip part decomposition even if configured |
| `--no-animate` | Skip animation even for rigged models |
| `--no-lod` | Skip LOD generation even if enabled |
| `--no-collision` | Skip collision mesh generation even if enabled |
| `--profile-tools` | Enable CPU/RAM/GPU profiling via `GAMEDEV_PROFILE` |
| `--profile-log FILE.jsonl` | Profiler log output |
| `--low-vram` | Deprecated no-op (sub-tools auto-detect VRAM via hw-auto) |
| `--force` | Regenerate everything (ignore existing outputs) |
| `--gpu-ids "0,1"` | Multi-GPU IDs (auto-detected via `nvidia-smi` if omitted) |
| `--no-dashboard` | Simple progress bars instead of TUI dashboard |
| `--plain` | Plain text output (no Rich/TUI, for scripts) |

**Key behaviors:**

- **Exclusive lock:** `.gameassets_batch.lock` (fcntl) prevents two batches in the same folder. `--skip-batch-lock` disables.
- **VRAM preflight:** warns if free VRAM < ~1.8 GiB. `--skip-gpu-preflight` disables.
- **CUDA:** sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` if unset.
- **Multi-GPU:** `--gpu-ids 0,1` propagates `CUDA_VISIBLE_DEVICES` and `--gpu-ids` to all sub-tools.
- **JSONL log:** each record includes `timings_sec` per subprocess (e.g., `image_text2d`, `text3d`, `paint3d_texture`), `audio_path` / `audio_error` when applicable.
- **Dashboard TUI:** default rich dashboard shows per-asset progress through pipeline stages. Use `--no-dashboard` or `--plain` for headless / CI.

**Batch mode:** Text2D uses `generate-batch` (JSONL manifest) for efficiency; Texture2D rows run individually per row.

### `gameassets resume`

```bash
gameassets resume --profile game.yaml --manifest manifest.yaml [options]
```

Smart resume: analyzes each asset's state on disk and runs only pending phases. Safe to re-run after interruption.

| Flag | Description |
|------|-------------|
| `--profile` | Game profile YAML |
| `--manifest` | Asset manifest |
| `--presets-local FILE` | Custom presets |
| `--log FILE.jsonl` | JSONL log output |
| `--dry-run` | Show plan without executing |
| `--fail-fast` | Stop on first error |
| `--work-dir DIR` | Persistent work directory for shapes (default: `.gameassets_work/`) |
| `--force` | Regenerate everything |
| `--gpu-ids "0,1"` | Multi-GPU IDs |
| `--no-dashboard` | Simple progress bars |

**State detection per asset:**

| State | Condition |
|-------|-----------|
| `_ROW_NEED_IMAGE` | PNG missing |
| `_ROW_NEED_SHAPE` | Shape GLB missing |
| `_ROW_NEED_PAINT` | Painted GLB missing (when `paint3d` configured) |
| `_ROW_NEED_RIG` | Rigged GLB missing |
| `_ROW_NEED_ANIMATE` | Animated GLB missing |
| `_ROW_NEED_LOD` | LOD meshes missing |
| `_ROW_NEED_COLLISION` | Collision mesh missing |
| `_ROW_DONE` | All outputs present |

Resume shows a plan table before execution:

```
┌────────────────────────────────────┬───────────┬──────────────────────────┐
│ Phase                              │ Pending   │ Action                   │
├────────────────────────────────────┼───────────┼──────────────────────────┤
│ 1. Image (text2d)                  │ 3         │ text2d generate          │
│ 2. Shape (hunyuan)                 │ 5         │ text3d generate-batch    │
│ 3. Paint (textura + PBR no GLB)    │ 4         │ paint3d texture          │
│ 4. Rigging                         │ 2         │ rigging3d pipeline       │
│ 5. Animation                       │ 2         │ animator3d game-pack     │
│ Concluídos                         │ 5         │ skip                     │
└────────────────────────────────────┴───────────┴──────────────────────────┘
```

### `gameassets dream`

```bash
gameassets dream "description" [options]
```

Idea-to-game: an LLM plans assets + scene from a natural language description, generates everything via batch + skymap, and scaffolds a playable Vite project with `world.xml`, `main.ts`, `index.html`.

| Flag | Description |
|------|-------------|
| `DESCRIPTION` | Game description in natural language |
| `--output-dir DIR` | Project root (default: `.`) |
| `--llm-provider` | `openai` (default), `huggingface`, `stdin` |
| `--llm-model` | LLM model (e.g., `gpt-4o-mini`, `meta-llama/Llama-3.1-8B-Instruct`) |
| `--llm-api-key` | API key (overrides `OPENAI_API_KEY`) |
| `--llm-base-url` | OpenAI-compatible base URL |
| `--style-preset` | Override style preset name |
| `--max-assets` | Maximum number of assets (default: 8) |
| `--with-audio / --no-audio` | Include audio assets (default: on) |
| `--with-sky / --no-sky` | Generate equirectangular sky (default: on) |
| `--terrain / --no-terrain` | Enable/disable terrain (default: auto via LLM plan) |
| `--terrain-seed` | Override terrain seed |
| `--terrain-size` | Heightmap resolution (default: 1024) |
| `--terrain-world-size` | World size in meters (default: 256) |
| `--terrain-max-height` | Max terrain height (default: 50) |
| `--presets-local FILE` | Custom presets YAML |
| `--dry-run` | Generate files without running batch/sky (no GPU) |
| `--plan-json FILE.json` | Export dream plan as JSON |
| `--low-vram` | Deprecated no-op (sub-tools auto-detect VRAM via hw-auto) |

**LLM Providers:**

| Provider | Default Model | SDK |
|----------|--------------|-----|
| `openai` | gpt-4o-mini | OpenAI SDK (`openai`) |
| `huggingface` | Llama-3.1-8B | HuggingFace `InferenceClient` |
| `stdin` | — | Pipe to any CLI LLM |

Fallback: keyword-based plan if LLM call fails.

**Dream output structure:**

```
output_dir/
├── game.yaml            # Generated profile
├── manifest.yaml        # Generated asset manifest
├── world.xml            # VibeGame scene definition
├── main.ts              # Vite entry point
├── index.html           # HTML shell
├── dream_plan.json      # LLM plan (if --plan-json)
├── images/              # Generated 2D images
├── meshes/              # Generated GLBs
├── audio/               # Generated audio
└── sky.png              # Equirectangular sky
```

### `gameassets handoff`

```bash
gameassets handoff --profile game.yaml --manifest manifest.yaml \
  --public-dir PUBLIC/ [options]
```

Copy GLBs, audio, textures, and PBR maps from `output_dir` to a Vite `public/` directory. Generates `gameassets_handoff.json` manifest for the web runtime.

| Flag | Description |
|------|-------------|
| `--profile` | Game profile YAML |
| `--manifest` | Asset manifest |
| `--presets-local FILE` | Custom presets |
| `--public-dir DIR` | Vite `public/` directory (**required**) |
| `--copy / --symlink` | Copy files (default) or create symlinks |
| `--prefer-animated / --no-prefer-animated` | Prefer animated GLB (default: on) |
| `--prefer-rigged / --no-prefer-rigged` | Prefer rigged GLB (default: on) |
| `--prefer-parts / --no-prefer-parts` | Prefer parts GLB (default: off) |
| `--with-textures / --no-with-textures` | Also copy 2D PNGs |
| `--audio-format` | `copy` (default), `wav`, `ogg` |
| `--sfx-sample-rate` | OGG sample rate for SFX (default: 22050) |
| `--bgm-sample-rate` | OGG sample rate for BGM (default: 44100) |
| `--dry-run` | Show manifest JSON without writing files |

**Mesh selection priority:** `animated` > `rigged` > `parts` > `base`

**Output layout:**

```
public/
└── assets/
    ├── models/
    │   ├── chest_01.glb
    │   ├── chest_01_lod0.glb
    │   ├── chest_01_lod1.glb
    │   ├── chest_01_lod2.glb
    │   ├── chest_01_collision.glb
    │   └── hero_01.glb
    ├── audio/
    │   └── chest_01.wav
    ├── textures/
    │   └── chest_01.png
    ├── pbr/
    │   └── chest_01/
    │       ├── normal.png
    │       ├── metallic.png
    │       ├── roughness.png
    │       └── ao.png
    └── gameassets_handoff.json
```

### `gameassets validate`

```bash
gameassets validate --profile game.yaml --manifest manifest.yaml \
  [--max-poly-count 100000] [--max-file-size-mb 50]
```

Validate generated assets against quality thresholds. Checks: GLB existence, file size, poly count, texture presence, LODs, collision mesh, audio.

| Flag | Default | Description |
|------|---------|-------------|
| `--max-poly-count` | 100,000 | Maximum face count per mesh |
| `--max-file-size-mb` | 50.0 | Maximum file size in MB |

Exits with code 1 if any errors found.

### `gameassets mesh reorigin-feet`

```bash
gameassets mesh reorigin-feet PATH [--recursive/--no-recursive] \
  [--dry-run] [--exclude PATTERN]
```

Repositions each GLB so the mesh base sits at Y=0 with center at XZ (glTF Y-up convention). Moves the entire scene (single offset per file).

| Flag | Description |
|------|-------------|
| `PATH` | File or directory path |
| `--recursive` | Process subdirectories (default: yes) |
| `--dry-run` | List files without modifying |
| `--exclude` | fnmatch pattern to skip (repeatable, e.g., `hero.glb`, `*player*`) |

> **Warning:** Models with armatures / animations may break. Prefer static props only. Requires `bpy`.

### `gameassets debug`

Visual GLB debugging tools (delegates to Animator3D). Requires `animator3d` on `PATH` or `ANIMATOR3D_BIN`.

#### `gameassets debug screenshot`

```bash
gameassets debug screenshot INPUT [--output-dir DIR] [--views VIEWS] \
  [--resolution PX] [--show-bones] [--frame N] [--frame-list 1,36,72]
```

Multi-angle screenshots. Default views: `front,three_quarter,right,back`.

#### `gameassets debug inspect`

```bash
gameassets debug inspect INPUT [--output FILE.json]
```

JSON metadata dump (mesh, armature, animation, materials, bounds).

#### `gameassets debug compare`

```bash
gameassets debug compare FILE_A FILE_B [--output-dir DIR] \
  [--views front,three_quarter] [--resolution PX] [--with-inspect]
```

Side-by-side comparison with screenshots + report JSON.

#### `gameassets debug bundle`

```bash
gameassets debug bundle INPUT [--output-dir DIR] [--views VIEWS] \
  [--resolution PX] [--show-bones] [--frame N] [--frame-list N,N]
```

Full bundle for AI agents: inspect JSON + screenshots + `bundle.json` with metadata. Default views include `low_front` and `worm`.

> **Note:** For advanced mesh comparison with image metrics (MAE, RMSE, SSIM), use [GameDevLab](../GameDevLab) (`gamedev-lab debug compare --image-metrics`).

### `gameassets skill install`

```bash
gameassets skill install [-t TARGET_DIR] [--force]
```

Installs the Cursor Agent Skill (`SKILL.md`) to `.cursor/skills/gameassets/` in the game project.

## Manifest Reference

The manifest YAML defines the list of assets to generate. Each row specifies an asset with its idea, pipeline flags, and optional per-row overrides.

```yaml
assets:
  - id: chest_01                        # REQUIRED — unique identifier
    idea: "wooden chest with gold locks" # REQUIRED — description
    kind: prop                          # prop | character | environment
    pipeline: [3d, audio, rig, animate, parts, lod, collision]
    image_source: texture2d             # text2d | texture2d | skymap2d
    category: humanoid                  # Asset category (drives hints/params)
    lod_levels: 3                       # Number of LOD levels (default: 3)
    generation: medium                  # Quality profile override

    # Per-row audio config
    audio:
      duration: 2.0
      profile: effects                  # music | effects
      trim: true
      preset: null
      steps: null
      cfg_scale: null

    # Per-row Part3D config
    part3d:
      steps: 50
      octree_resolution: 256
      segment_only: false
```

### ManifestRow Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | **required** | Unique asset identifier (may include subpaths, e.g., `Props/crate_01`) |
| `idea` | `str` | **required** | Natural language description |
| `kind` | `str` | `None` | `prop`, `character`, `environment` |
| `pipeline` | `list[str]` | `[]` | Pipeline keywords: `3d`, `audio`, `rig`, `animate`, `parts`, `lod`, `collision` |
| `image_source` | `str` | `None` | Override profile `image_source` for this row (`text2d` / `texture2d` / `skymap2d`) |
| `category` | `str` | `""` | Asset category (e.g., `humanoid`, `chest`, `weapon`) |
| `lod_levels` | `int` | `3` | Number of LOD levels to generate |
| `generation` | `str` | `None` | Quality profile override (`fast` / `low` / `medium` / `high` / `highest`) |
| `audio.duration` | `float` | `None` | Audio duration override |
| `audio.profile` | `str` | `None` | `music` or `effects` |
| `audio.trim` | `bool` | `None` | Trim silence from audio |
| `audio.preset` | `str` | `None` | Text2Sound preset |
| `audio.steps` | `int` | `None` | Inference steps |
| `audio.cfg_scale` | `float` | `None` | CFG scale |
| `part3d.steps` | `int` | `None` | Part3D inference steps |
| `part3d.octree_resolution` | `int` | `None` | Part3D octree resolution |
| `part3d.segment_only` | `bool` | `None` | Only P3-SAM (no X-Part multi-part GLB) |

### Pipeline Keywords

| Keyword | Stage | Tool |
|---------|-------|------|
| `3d` | 3D generation | Text3D |
| `audio` | Audio generation | Text2Sound |
| `rig` | Auto-rigging | Rigging3D |
| `animate` | Animation clips | Animator3D |
| `parts` | Semantic decomposition | Part3D |
| `lod` | Level of detail | Text3D `lod` |
| `collision` | Collision mesh | Text3D `collision` |

## game.yaml Reference

The game profile configures style, output layout, and per-tool settings.

### Root Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | `str` | **required** | Game title (not injected into image prompts) |
| `genre` | `str` | **required** | Game genre (affects prompt mood) |
| `tone` | `str` | **required** | Visual tone / mood |
| `style_preset` | `str` | **required** | Built-in or custom preset name |
| `negative_keywords` | `list[str]` | `[]` | Words to avoid in prompts |
| `output_dir` | `str` | `"."` | Root output directory |
| `path_layout` | `str` | `"split"` | `"split"` (images/ + meshes/ subdirs) or `"flat"` (same dir per asset) |
| `images_subdir` | `str` | `"images"` | Images subdirectory (with `split` layout) |
| `meshes_subdir` | `str` | `"meshes"` | Meshes subdirectory (with `split` layout) |
| `audio_subdir` | `str` | `"audio"` | Audio subdirectory |
| `image_ext` | `str` | `"png"` | `png` / `jpg` / `jpeg` |
| `seed_base` | `int` | `None` | Base seed (per-asset seeds derived from `id`) |
| `image_source` | `str` | `"text2d"` | Default image tool: `text2d` / `texture2d` / `skymap2d` |
| `generation` | `str` | `None` | Quality profile: `fast` / `low` / `medium` / `high` / `highest` |

### Folder Layout (`path_layout`)

- **`split`** — `output_dir/images/<id>.png` and `output_dir/meshes/<id>.glb`. `id` may include subpaths (e.g., `Props/crate_01`).
- **`flat`** — `output_dir/<dirname>/<basename>.png` and same dir for GLB. E.g., `id` = `Collectibles/core` → `output_dir/Collectibles/core.png` and `Collectibles/core.glb`. Good for Godot-style one-folder-per-category layouts.

### Tool Blocks

#### `text2d` — Text2DProfile

Options passed to the `text2d generate` CLI (FLUX Klein).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `low_vram` | `bool` | `false` | Deprecated no-op (hw-auto handles small GPUs) |
| `cpu` | `bool` | `false` | CPU-only inference |
| `width` | `int` | `None` | Image width (overridden by generation profile) |
| `height` | `int` | `None` | Image height (overridden by generation profile) |
| `steps` | `int` | `None` | Inference steps |
| `guidance_scale` | `float` | `None` | CFG guidance scale |

#### `texture2d` — Texture2DProfile

Options for HF seamless texture generation + optional Materialize PBR maps.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `width` | `int` | `None` | Texture width |
| `height` | `int` | `None` | Texture height |
| `steps` | `int` | `None` | Inference steps |
| `guidance_scale` | `float` | `None` | Guidance scale |
| `negative_prompt` | `str` | `None` | Negative prompt override |
| `preset` | `str` | `None` | Texture2D preset |
| `cfg_scale` | `float` | `None` | CFG scale |
| `lora_strength` | `float` | `None` | LoRA strength |
| `model_id` | `str` | `None` | HF model ID |
| `materialize` | `bool` | `false` | Generate PBR maps via Materialize |
| `materialize_bin` | `str` | `None` | Materialize binary path |
| `materialize_format` | `str` | `"png"` | PBR map format: `png` / `jpg` / `tga` / `exr` |
| `materialize_quality` | `int` | `95` | JPEG/TGA quality (0–100) |
| `materialize_verbose` | `bool` | `false` | Verbose Materialize output |
| `materialize_maps_subdir` | `str` | `"pbr_maps"` | Subdirectory for PBR maps |

#### `skymap2d` — Skymap2DProfile

Options for 360° equirectangular sky generation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `width` | `int` | `None` | Image width |
| `height` | `int` | `None` | Image height |
| `steps` | `int` | `None` | Inference steps |
| `guidance_scale` | `float` | `None` | Guidance scale |
| `negative_prompt` | `str` | `None` | Negative prompt |
| `preset` | `str` | `None` | Skymap2D preset |
| `cfg_scale` | `float` | `None` | CFG scale |
| `lora_strength` | `float` | `None` | LoRA strength |
| `model_id` | `str` | `None` | HF model ID |

#### `text3d` — Text3DProfile

Options for Text3D (Hunyuan3D-2.1) shape generation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `preset` | `str` | `None` | `fast` / `balanced` / `hq` |
| `low_vram` | `bool` | `false` | Deprecated no-op (hw-auto handles small GPUs via SDNQ INT4) |
| `export_origin` | `str` | `"feet"` | `feet` / `center` / `none` |
| `steps` | `int` | `None` | Inference steps |
| `octree_resolution` | `int` | `None` | Octree resolution |
| `num_chunks` | `int` | `None` | Number of chunks |
| `mc_level` | `float` | `None` | Marching cubes level |
| `allow_shared_gpu` | `bool` | `false` | Allow sharing GPU with other processes |
| `gpu_kill_others` | `bool` | `true` | Kill other GPU processes |
| `full_gpu` | `bool` | `false` | Use full GPU |
| `model_subfolder` | `str` | `None` | Model subfolder override |
| `guidance` | `float` | `None` | Hunyuan guidance value |
| `simplify_texture_size` | `int` | `None` | Texture size for `remesh-textured` |

> **Quality note:** `low_vram` is a deprecated no-op — hw-auto applies SDNQ INT4 automatically on small GPUs (~6 GB). For best results, use `preset: balanced` or `fast`.

#### `paint3d` — Paint3DProfile

Options for Paint3D texturing (Hunyuan3D-Paint 2.1 AI or quick paint).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `style` | `str` | `"hunyuan"` | `hunyuan` (Paint 2.1 AI) / `solid` / `perlin` |
| `preserve_origin` | `bool` | `true` | Preserve mesh origin during paint |
| `max_views` | `int` | `None` | Max render views (overridden by generation profile) |
| `view_resolution` | `int` | `None` | View render resolution |
| `render_size` | `int` | `None` | Render size |
| `texture_size` | `int` | `None` | Output texture size |
| `bake_exp` | `int` | `None` | View blending sharpness (default: 6 from profile) |
| `smooth` | `bool` | `true` | Bilateral smooth on painted mesh |
| `smooth_passes` | `int` | `None` | Number of smooth passes |
| `low_vram_mode` | `bool` | `false` | Deprecated no-op (hw-auto handles small GPUs) |
| `solid_color` | `str` | `"#888888"` | Quick paint solid color |
| `perlin_tint` | `str` | `"#7a7268"` | Quick paint Perlin tint |
| `perlin_frequency` | `float` | `4.0` | Perlin noise frequency |
| `perlin_octaves` | `int` | `4` | Perlin noise octaves |
| `perlin_contrast` | `float` | `0.55` | Perlin noise contrast |
| `perlin_seed` | `int` | `None` | Perlin noise seed |

#### `text2sound` — Text2SoundProfile

Options for Text2Sound (Stable Audio Open) audio generation.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `quality` | `str` | `"medium"` | QualityEngine quality tier |
| `category` | `str` | `None` | Asset category for audio hints |
| `duration` | `float` | `None` | Audio duration |
| `steps` | `int` | `None` | Inference steps |
| `cfg_scale` | `float` | `None` | CFG scale |
| `audio_format` | `str` | `"wav"` | `wav` / `flac` / `ogg` |
| `preset` | `str` | `None` | Text2Sound preset |
| `sigma_min` | `float` | `None` | Sigma min |
| `sigma_max` | `float` | `None` | Sigma max |
| `sampler` | `str` | `None` | Sampler name |
| `trim` | `bool` | `None` | Trim leading silence |
| `model_id` | `str` | `None` | Model ID |
| `half_precision` | `bool` | `None` | `True` = `--half`, `False` = `--no-half`, `None` = auto |

#### `rigging3d` — Rigging3DProfile

Options for UniRig auto-rigging after Text3D.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `output_suffix` | `str` | `"_rigged"` | Suffix for rigged GLB (e.g., `hero_rigged.glb`) |
| `root` | `str` | `None` | UniRig package root (like `RIGGING3D_ROOT`) |
| `python` | `str` | `None` | Python interpreter path (like `RIGGING3D_PYTHON`) |

> **Presence** of a `rigging3d` block enables rigging for character rows even without `rig` in the manifest pipeline.

#### `animator3d` — Animator3DProfile

Options for Animator3D `game-pack` after rigging.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `preset` | `str` | `"humanoid"` | `humanoid` / `creature` / `flying` |

> **Presence** of an `animator3d` block enables auto-animation after successful rig.

#### `part3d` — Part3DProfile

Options for Part3D semantic decomposition after Text3D, before rigging.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `octree_resolution` | `int` | `None` | Octree resolution |
| `steps` | `int` | `None` | Inference steps |
| `num_chunks` | `int` | `None` | Number of chunks |
| `segment_only` | `bool` | `false` | Only P3-SAM (mesh with per-part colors, no X-Part) |
| `verbose` | `bool` | `false` | Verbose output |
| `parts_suffix` | `str` | `"_parts"` | Multi-part GLB suffix |
| `segmented_suffix` | `str` | `"_segmented"` | Segmented mesh suffix |
| `no_quantize_dit` | `bool` | `false` | Never quantize the DiT |
| `torch_compile` | `bool` | `false` | Enable torch.compile |
| `no_attention_slicing` | `bool` | `false` | Disable attention slicing |

> **VRAM note:** `low_vram_mode`, `no_cpu_offload` and `quantization` are deprecated no-ops — Part3D auto-detects via hw-auto (`PART3D_HW_AUTO`). Existing `game.yaml` entries with these keys are silently ignored.

#### `lod` — LODProfile

Level-of-detail triplet generation via `text3d lod`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `lod1_ratio` | `float` | `0.42` | LOD1 face ratio (must be > `lod2_ratio`) |
| `lod2_ratio` | `float` | `0.14` | LOD2 face ratio (must be > 0) |
| `min_faces_lod1` | `int` | `500` | Minimum faces for LOD1 |
| `min_faces_lod2` | `int` | `150` | Minimum faces for LOD2 |
| `meshfix` | `bool` | `false` | Apply mesh repair before decimation |

#### `collision` — CollisionProfile

Collision mesh generation via `text3d collision` (convex hull).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_faces` | `int` | `300` | Maximum faces for collision mesh (≥ 4) |
| `convex_hull` | `bool` | `true` | Use convex hull decomposition |

## Asset Categories

Each asset is classified into a category via `infer_category()` keyword matching (supports Portuguese terms) or explicit `category` field. The category determines **target face count** for `text3d remesh-textured` and injects category-specific hints into prompts.

| Category | Target Faces | Default Kind | Hints |
|----------|-------------|-------------|-------|
| `humanoid` | 32,000 | character | Full-body concept, A/T-pose for rig, clear limb separation |
| `creature` | 24,000 | character | Creature concept, side view, organic mesh, no rider |
| `chest` | 8,000 | prop | Container with lid, no scattered loot |
| `weapon` | 6,000 | prop | Single clean weapon, no hands holding it |
| `tree` | 12,000 | environment | Full tree with trunk + canopy separation |
| `rock` | 3,200 | prop | Simple boulder, low-poly friendly |
| `mineral` | 4,800 | prop | Crystal with distinct facets, no ground attachment |
| `building` | 24,000 | environment | Isolated structure, no terrain, clean geometry |
| `furniture` | 8,000 | prop | Isolated item, no room, visible part lines |
| `vegetation` | 10,000 | environment | Clean stem and leaf separation, no ground |
| `vehicle` | 16,000 | prop | No driver, clean mechanical geometry |
| `armor` | 10,000 | prop | No body inside, clean plate geometry |
| `food` | 3,200 | prop | Simple organic shape, no plate/table |
| `tool` | 4,000 | prop | Handle and head, no hands |
| `terrain` | 16,000 | environment | Ground chunk, flat bottom |
| `effects` | 2,000 | prop | Abstract, very low complexity |

Categories also provide per-category `extra_negatives` to avoid common artifacts (e.g., humanoid excludes "holding sword", "wearing armor"; creature excludes "ridden by character").

## Generation Quality Profiles

Quality profiles tune all tools simultaneously for a speed/quality trade-off. Set via `generation:` in `game.yaml` (root level) or per-row `generation:` in the manifest.

Explicit tool settings in `game.yaml` **always win** over the profile default. The profile only fills `None` / default fields.

| Profile | Text2D Size | Text2D Steps | Text3D Preset | Text3D Guidance | Paint Views | Paint ViewRes | Paint Render | Paint TexSize | Paint BakeExp | Simplify Ratio | Simplify TexSize | Text2Sound Steps |
|---------|------------|--------------|---------------|-----------------|-------------|---------------|--------------|---------------|---------------|----------------|------------------|-----------------|
| `fast` | 512×512 | 4 | `fast` | 5.0 | 2 | 384 | 1024 | 1024 | 6 | 0.25 | 1024 | 4 |
| `low` | 768×768 | 4 | `fast` | 5.0 | 4 | 384 | 1024 | 2048 | 6 | 0.5 | 1024 | 8 |
| `medium` | 1024×1024 | 4 | `balanced` | 5.0 | 6 | 512 | 2048 | 2048 | 6 | 1.0 | 2048 | 16 |
| `high` | 1024×1024 | 8 | `hq` | 5.0 | 8 | 512 | 2048 | 4096 | 6 | 2.0 | 2048 | 24 |
| `highest` | 1024×1024 | 8 | `hq` | 5.0 | 10 | 512 | 2048 | 4096 | 6 | 0.0 (no simplify) | 4096 | 32 |

**Paint3D settings:** all profiles use `smooth: true` and 3 smooth passes (except `fast` which uses 2).

**Simplify ratio:** multiplier for category target faces (`0.25` = quarter of target, `0.0` = skip simplification entirely).

## Style Presets

Style presets inject `prompt_prefix` and `negative_suffix` into generated prompts. Defined in `src/gameassets/data/presets.yaml` and extendable via `--presets-local FILE.yaml`.

### Built-in Presets

| Preset | Description | `prompt_prefix` |
|--------|-------------|-----------------|
| `lowpoly` | Low poly stylized, clean topology, flat shading | `low poly 3D game asset, clean topology, flat shading, subtle ambient occlusion...` |
| `pixel_art` | Pixel art / retro 16-bit, limited palette, crisp pixels | `pixel art game sprite style, limited palette, crisp pixels, 16-bit era...` |
| `painterly` | Digital painting, visible brush strokes, fantasy aesthetic | `hand-painted game art, visible brush strokes, stylized lighting...` |
| `realistic_stylized` | PBR-friendly, studio lighting, game engine ready | `stylized realistic game asset, PBR-friendly materials, clean detail...` |

### Custom Presets

Create a YAML file with the same structure:

```yaml
# presets-local.yaml
galaxy_orbital:
  label: "Galaxy orbital sci-fi"
  prompt_prefix: "sci-fi game asset, holographic, orbital, deep space colors, neon accents"
  negative_suffix: "fantasy, medieval, rustic, organic"
  hint_2d: "futuristic silhouette, tech details"
  hint_3d: "clean sci-fi mesh, emissive panels"
```

Pass via `--presets-local presets-local.yaml`. Local presets merge with (and override) built-in presets.

## Environment Variables

| Variable | Tool | Description |
|----------|------|-------------|
| `TEXT2D_BIN` | Text2D | Path to `text2d` executable (if not on PATH) |
| `TEXTURE2D_BIN` | Texture2D | Path to `texture2d` executable |
| `SKYMAP2D_BIN` | Skymap2D | Path to `skymap2d` executable |
| `TEXT3D_BIN` | Text3D | Path to `text3d` executable |
| `PAINT3D_BIN` | Paint3D | Path to `paint3d` executable (when `paint3d` block configured) |
| `TEXT2SOUND_BIN` | Text2Sound | Path to `text2sound` executable |
| `PART3D_BIN` | Part3D | Path to `part3d` executable |
| `RIGGING3D_BIN` | Rigging3D | Path to `rigging3d` executable |
| `RIGGING3D_ROOT` | Rigging3D | UniRig package root directory |
| `RIGGING3D_PYTHON` | Rigging3D | Python interpreter for rigging |
| `ANIMATOR3D_BIN` | Animator3D | Path to `animator3d` executable |
| `MATERIALIZE_BIN` | Materialize | Path to `materialize` executable (Texture2D + PBR only) |
| `TERRAIN3D_BIN` | Terrain3D | Path to `terrain3d` executable (dream terrain only) |
| `OPENAI_API_KEY` | Dream LLM | API key for OpenAI LLM provider |
| `OPENAI_BASE_URL` | Dream LLM | OpenAI-compatible base URL |
| `GAMEDEV_PROFILE` | Profiling | Enable CPU/RAM/GPU profiling (`--profile-tools`) |
| `GAMEDEV_PROFILE_LOG` | Profiling | Profiler JSONL log path |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA | Auto-set to `expandable_segments:True` if empty (reduces fragmentation) |

## Output Layout

With `path_layout: split` (default):

```
output_dir/
├── images/
│   ├── chest_01.png
│   ├── hero_01.png
│   └── tree_01.png
├── meshes/
│   ├── chest_01.glb              # Final painted mesh
│   ├── chest_01_parts.glb        # Semantic parts (if enabled)
│   ├── chest_01_segmented.glb    # Per-part colors (if enabled)
│   ├── chest_01_rigged.glb       # Rigged mesh (if enabled)
│   ├── chest_01_animated.glb     # Animated mesh (if enabled)
│   ├── chest_01_lod0.glb         # LOD levels (if enabled)
│   ├── chest_01_lod1.glb
│   ├── chest_01_lod2.glb
│   └── chest_01_collision.glb    # Collision mesh (if enabled)
└── audio/
    ├── chest_01.wav
    └── hero_01.wav
```

With `path_layout: flat`:

```
output_dir/
├── Props/
│   ├── chest_01.png
│   └── chest_01.glb
└── Characters/
    ├── hero_01.png
    └── hero_01.glb
```

With Texture2D + Materialize (PBR from diffuse):

```
output_dir/
├── images/
│   └── floor_tile_01.png
├── pbr_maps/
│   └── floor_tile_01/
│       ├── normal.png
│       ├── metallic.png
│       ├── smoothness.png        # Renamed to roughness in handoff
│       └── ao.png
└── meshes/
    └── ...
```

## Pipeline Integration

### Batch Workflow

```bash
# 1. Initialize
gameassets init --path ./my_game
cd my_game

# 2. Edit game.yaml (style, tool settings) and manifest.yaml (asset list)

# 3. Preview prompts (no GPU)
gameassets prompts --profile game.yaml --manifest manifest.yaml

# 4. Full pipeline
gameassets batch --profile game.yaml --manifest manifest.yaml --log run.jsonl

# 5. Resume after interruption
gameassets resume --profile game.yaml --manifest manifest.yaml

# 6. Validate outputs
gameassets validate --profile game.yaml --manifest manifest.yaml

# 7. Handoff to Vite/VibeGame
gameassets handoff --profile game.yaml --manifest manifest.yaml \
  --public-dir ../my-game/public
```

### Dream Workflow (Idea → Game)

```bash
# Dry-run: generate files without GPU
gameassets dream "3D platformer with crystals in a cloud world" --dry-run

# Full execution
gameassets dream "3D platformer with crystals in a cloud world" \
  --output-dir ./cloud-platformer \
  --llm-provider openai \
  --style-preset lowpoly

# With terrain
gameassets dream "open world RPG with forests and mountains" \
  --terrain --terrain-size 2048 --terrain-world-size 512
```

### VibeGame Integration

After handoff, GLBs are available at `/assets/models/` in the Vite project. Use the VibeGame APIs to load them:

- `loadGltfToScene` — Basic GLB loading
- `loadGltfAnimated` — GLB with animation support
- `loadGltfToSceneWithAnimator` — GLB with full animator control
- Declarative: `<PlayerGLTF pos="0 0 0" model-url="/assets/models/hero.glb"></PlayerGLTF>` in `world.xml`

For PBR sky environments: `applyEquirectSkyEnvironment` from `vibegame` (`VibeGame/src/extras/sky-env.ts`).

See [`docs/MONOREPO_GAME_PIPELINE.md`](../docs/MONOREPO_GAME_PIPELINE.md) for the full pipeline layout and [`VibeGame/examples/`](../VibeGame/examples/) for working examples.

## Development

### Setup

```bash
cd Shared && pip install -e .                  # Shared dependency
cd GameAssets && pip install -e ".[dev]"       # GameAssets + dev deps
```

### Tests

```bash
cd GameAssets
pytest tests/                                  # Run all tests
pytest tests/test_manifest.py                  # Single test file
pytest -k "test_name_pattern"                  # By keyword
pytest --cov=src --cov-report=html             # With coverage
```

### Lint / Format

```bash
ruff check .                                   # Lint
ruff check . --fix                             # Auto-fix
ruff format .                                  # Format
ruff format --check .                          # Check formatting
```

### From repo root

```bash
make test-gameassets     # Run GameAssets tests
make check               # Full CI (lint + format + typecheck + all tests)
```

### Project Layout

```
GameAssets/
├── src/gameassets/
│   ├── cli.py                  # Click CLI (init, prompts, batch, handoff, dream, info, validate, debug, mesh, skill)
│   ├── cli_rich.py             # Rich-click configuration
│   ├── profile.py              # game.yaml parsing (GameProfile + all sub-profiles)
│   ├── manifest.py             # manifest.yaml parsing (ManifestRow)
│   ├── prompt_builder.py       # Prompt construction from profile + preset
│   ├── generation_profiles.py  # Quality presets (fast/low/medium/high/highest)
│   ├── categories.py           # Asset categories with target faces and hints
│   ├── presets.py              # Style preset loading (bundled + local merge)
│   ├── templates.py            # Prompt templates for init
│   ├── batch_cmd.py            # batch command implementation
│   ├── resume_cmd.py           # resume command implementation
│   ├── handoff_export.py       # handoff command implementation
│   ├── pipeline.py             # Subprocess argument builders per tool
│   ├── runner.py               # Subprocess execution utilities
│   ├── batch_guard.py          # Exclusive lock, GPU detection, VRAM preflight
│   ├── helpers.py              # Shared helper functions
│   ├── param_optimizer.py      # Text3D parameter optimization for target faces
│   ├── paths.py                # Path resolution, state classification
│   ├── mesh_reorigin.py        # GLB reorigin (bpy)
│   ├── dashboard.py            # Rich TUI dashboard for batch progress
│   ├── validator.py            # Asset validation
│   ├── dream/                  # Idea-to-game subsystem
│   │   ├── planner.py          # LLM-based game planning
│   │   ├── runner.py           # Dream execution (batch + sky + scaffold)
│   │   ├── emitter.py          # world.xml / main.ts / index.html generation
│   │   └── llm_context.py      # LLM prompt context
│   └── data/
│       └── presets.yaml        # Bundled style presets
├── tests/
│   └── test_*.py
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   └── setup.sh
├── pyproject.toml
└── README.md
```

## License

- **Code:** MIT (aligned with the rest of the monorepo).
- **Invoked models** (`text2d`, `texture2d`, `skymap2d`, `text2sound`, `text3d`, `part3d`, `rigging3d`): each tool downloads or uses weights under its own license (FLUX, Tencent Hunyuan, Stability Audio, UniRig, etc.). **Do not** confuse the MIT `gameassets` code with checkpoint licenses. See [monorepo README — Licenses](../README.md).
