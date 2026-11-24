# Texture2D — Seamless 2D Texture Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for **seamless (tileable) 2D textures** using **Stable Diffusion v1.5**, running locally on GPU.

**Seamless 2.0** — tiling via three complementary layers:

1. **`--seamless-mode late` (default)** — noise rolling (latents rolled by half each step) + circular conv padding only in the final ~20% of steps. Recipe from [pattern-diffusion](https://huggingface.co/Arrexel/pattern-diffusion) (Apache 2.0): constant circular padding measurably hurts FID/CLIP; late+rolling keeps quality **and** tileability. `full` (circular from step 0, the classic behavior) and `off` (plain SD1.5) remain available.
2. **Controlled VAE decode** — the pipeline always outputs latents and the tool decodes them itself: **integral decode** (no tiling) whenever the resolution allows. The diffusers `tiled_decode` slices without wrapping and breaks the circular seam at the pixel level; when tiling is unavoidable (1024² on tight VRAM), the latent is circular-padded first and the result cropped (wrap-preserving).
3. **Hires via latent upscale + refine** — targets above 512² (the SD1.5 native resolution) are generated at native, then the latent is upscaled and refined with a short strength-controlled pass (classic hires-fix) instead of direct high-res generation (known to duplicate content). The `high`/`highest` tiers use this automatically.

Plus quality extras: **VAE ft-mse** by default (`TEXTURE2D_VAE_ID=none` to disable), seam terms in the base negative prompt, a **tileability score** in every JSON sidecar, and optional **auto-heal** of the seam band when the score falls below 0.85.

In the [AiGameKit](../README.md) monorepo, the package depends on [**aigamekit-shared**](../Shared/) (`aigamekit_shared`): quality presets, Rich CLI, GPU helpers, and shared conventions aligned with Text2D, Text3D, and GameAssets.

## Overview

- **Local GPU inference** — Stable Diffusion 1.5 + seamless 2.0, no cloud API needed; fits in ~2.5 GB VRAM (a 6 GiB GPU is plenty)
- **Seamless modes** — `late` (noise rolling + late circular; best quality), `full` (classic circular), `off`
- **Hires >512²** — native generation + latent upscale + refine (no direct above-native sampling)
- **Wrap-preserving decode** — integral VAE decode by default; circular-padded tiling only when VRAM demands it
- **Real CFG** — negative prompts work natively (`--negative-prompt`), no `true-cfg` 2x cost
- **Automatic seamless prompting** — appends tileable/seamless instructions automatically
- **Tileability score + auto-heal** — score in the JSON sidecar; optional cross-fade heal below 0.85
- **Request precision** — prompts fitted to the CLIP 77-token window without losing your prompt's tail; explicit flags (and a chosen `--quality`) win over the material preset; quality gates re-roll a seed that yields a knot, plank gaps or flecks the tiling would repeat (`--max-isolated-detail`, `--max-banding`, `--max-tail-ratio`); `--axis` turns a texture whose lines came out sideways; `--match-color` grades the result to the colour you asked for
- **13 material presets** — Wood, Stone, Grass, Sand, Dirt, Metal, Brick, Fabric, Leather, Concrete, Marble, Gravel, Tile Floor
- **Quality tiers** — `fast`, `low`, `medium` (default), `high`, `highest` via `--quality`
- **Batch generation** — multiple textures from a prompt file
- **Multi-GPU** — `--gpu-ids 0,1` splits weights across GPUs via accelerate
- **JSON metadata** — each texture has a `.json` sidecar with seed, final prompt, parameters, and tileability
- **Hardware auto-detection** — `--hw-auto` detects device and multi-GPU layout (on by default)

## Installation

### Official (monorepo)

At the **AiGameKit** repo root:

```bash
./install.sh texture2d
```

The installer creates `Texture2D/.venv`, editable-installs the package, and places a wrapper in `~/.local/bin`. See [docs/INSTALLING.md](../docs/INSTALLING.md) for details.

### Manual / development

```bash
cd Shared && pip install -e .
cd Texture2D && pip install -e .
```

Requires a **CUDA GPU** (PyTorch, diffusers, transformers, accelerate are runtime dependencies).

## Commands

| Command | Description |
|---------|-------------|
| `texture2d generate PROMPT` | Generate a seamless texture (delegates to vramd when available) |
| `texture2d presets` | List available material presets |
| `texture2d batch FILE` | Batch generate from a prompt file (one per line) |
| `texture2d server` | **Deprecated** — use `vramd start` (vramd) |
| `texture2d server-status` | **Deprecated** — use `vramd status` |
| `texture2d server-stop` | **Deprecated** — use `vramd stop` |
| `texture2d info` | Config, system, and environment info |
| `texture2d skill install` | Install Cursor Agent Skill |
| `texture2d validate-tileable` | Validate a texture's tileability (and, with `--max-isolated-detail`, `--max-banding`, `--max-tail-ratio`, the details the tiling would repeat, the lines across it and the flecks) |

### `texture2d generate PROMPT`

Generate a seamless tileable texture from a text prompt.

```bash
# Basic usage
texture2d generate "rough stone wall surface, medieval castle" -o stone.png

# With a material preset
texture2d generate "weathered surface" --preset Stone -o wall.png

# High quality with a fixed seed
texture2d generate "mossy cobblestone" --quality high --seed 42 -o cobble.png

# Native negative prompt (real CFG, no true-cfg cost)
texture2d generate "dark marble floor" -n "blurry, watermark" -o marble.png
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-o, --output` | path | auto (`outputs/textures/`) | Output file path (`.png`) |
| `-W, --width` | int | 512 | Image width (multiple of 8) |
| `-H, --height` | int | 512 | Image height (multiple of 8) |
| `-s, --steps` | int | 30 | Inference steps |
| `-g, --guidance` | float | 7.0 | Guidance scale (real CFG) |
| `--seed` | int | None | Random seed for reproducibility |
| `-n, --negative-prompt` | str | `""` | Negative prompt (works natively with SD1.5 CFG) |
| `-p, --preset` | str | None | Material preset (see Presets below) |
| `-m, --model` | str | None | HF model ID override (default: `stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `--cpu` | flag | `false` | Force CPU inference |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier: `fast`, `low`, `medium`, `high`, `highest` |
| `--hw-auto/--no-hw-auto` | flag | `on` | Hardware auto-detection (device + multi-GPU). No offload/clamp (SD1.5 fits any CUDA GPU) |
| `--group-offload/--no-group-offload` | flag | `on` | **Group offload + CUDA streams** when full-GPU wouldn't have headroom (shared/tight GPU; peak ≈ activation; VAE tiling + attention slicing as chunks). Kill-switch: `TEXTURE2D_GROUP_OFFLOAD=0` |
| `--ground` | str | `auto` | Top-down ground mode: applies viewpoint/lighting/scale prompt modifiers |
| `--seamless-mode` | str | `late` | `late` (roll + late circular, best quality) · `full` (circular from start) · `off` (plain SD1.5) |
| `--refine-steps` | int | 12 | Hires refine steps (targets >512²); tier `high` uses 12, `highest` 16 |
| `--vae-tiling/--no-vae-tiling` | flag | auto | VAE decode: auto = integral whenever it fits (preserves the seam); force wrap-preserving tiling with `--vae-tiling` |
| `--seam-heal/--no-seam-heal` | flag | on | Cross-fade heal of the border band when the tileability score < 0.85 |
| `--max-isolated-detail` | float | off | Threshold (0..1) for an isolated detail — a knot, a blotch that tiling repeats in every cell even with a perfect seam. Above it the seed is re-rolled (see `--attempts`). Recommended for wood: 0.12 |
| `--max-banding` | float | off | Threshold for axis banding — plank gaps or lines running across the whole texture (measured: 0.87 on planks, 0.68 on sideways grain, 0.20–0.44 on straight grain). Above it the seed is re-rolled. Recommended for wood: 0.5 |
| `--max-tail-ratio` | float | off | Threshold for a heavy tail — flecks or bright dots on a smooth background (measured: 28 on a flecked whitewash, 1.5–3.2 elsewhere). Above it the seed is re-rolled. Recommended for wood: 5 |
| `--axis` | `v`/`u` | off | The texture has a direction: `v` = lines run top to bottom (wood grain), `u` = left to right. Rotates 90° when they come out the other way — SD1.5 does not obey "vertical grain" |
| `--match-color` | `#RRGGBB` | off | Colour you asked for, as the mean colour. SD1.5 has a mid-tone prior (every wood prompt came out brown at 96–128 of 255): the image is graded to the target and keeps its structure (`match_color` in the sidecar) |
| `--attempts` | int | 1 | Maximum generations when a quality gate is set; if none passes, the one that comes closest is returned (the sidecar marks `quality_gates`) |
| `--no-hires` | flag | off | Generate directly at the requested resolution (skip 512 + refine) |
| `-v, --verbose` | flag | `false` | Verbose logging |

> **Note:** When `--quality` is set, resolution and steps are auto-filled from the quality profile **only if** the user didn't explicitly pass `-W`, `-H`, `-s`, or `-g`. Explicit flags always win (soft resolution via `QualityEngine`). The same holds against the material
preset: a preset only fills what you did **not** declare (an explicit `-s`/`-g`/`-W`/`-H`, or a
`--quality` you chose); with none of them the preset wins, as before.

### Request precision

SD1.5's text encoder reads 77 tokens per prompt — positive and negative. Anything past that is
silently dropped, without an error. Six guarantees:

- **Your prompt is never the part that falls off.** When `preset + prompt` fits, the text is the
  classic one (preset first). When it does not, your prompt comes whole and the preset only lends
  the clauses that fit — `preset_dropped` in the sidecar lists the rest. A prompt too long on its
  own is cut on a comma boundary and reported as `prompt_user_truncated`.
- **A negative prompt without waste.** Only when the negative overflows: duplicates are removed
  and clauses are kept by priority (the tool's base terms, then yours, then the preset's).
  `negative_dropped` says what stayed out.
- **No repeated oddity.** `isolated_detail.spot` in the sidecar measures how much the most
  salient small detail stands out from the rest of the texture (0..1, lower is better: the pool
  textures stay below ~0.10; a knot with a halo gives 0.17–0.30). With `--max-isolated-detail
  0.12` a failing seed is re-rolled with `seed + 1`. A blotch that fails on
  every seed is the **tiled VAE decode**, not the seed: it leaves a smear per
  tile (measured 0.40 against 0.08 with the integral decode). The automatic
  policy empties the PyTorch cache before measuring free VRAM, so a 6 GB GPU
  takes the integral decode; `--no-vae-tiling` forces it.
- **No planks, no sideways grain.** `banding` in the sidecar is, per axis, how
  much a whole line of the texture stands out (in units of the band-pass 99th
  percentile): plank gaps give 0.87, sideways grain 0.68, straight grain
  0.20–0.44. `--max-banding 0.5` re-rolls a seed with gaps; `--axis v` rotates
  a texture whose lines came out running left to right (only when one axis
  clearly dominates — a diagonal rope is left alone; `axis.rotated` says so).
- **No flecks.** Many small bright dots on a smooth background sit inside the
  99th percentile, so the isolated-detail gate misses them. `tail_ratio` is how
  far the 99.9th percentile sits from the median in units of the 95th (a dense
  texture gives 1.5–3.2; a flecked whitewash gave 28). `--max-tail-ratio 5`.
- **The colour you asked for.** SD1.5 has a mid-tone prior: nine wood prompts
  — from near-black pine to ivory whitewash — all came out brown at a mean
  luminance of 96–128 out of 255. `--match-color #RRGGBB` grades the image so
  its mean colour is the target, with a single contrast scale shared by all
  channels that keeps the structure (warm grain stays warm on a dark target)
  and only compresses contrast when the target is close to black or white
  (`match_color` in the sidecar: `before`, `after`, `scale`).

With several gates, every failing attempt re-rolls; if none passes, the one
with the lowest worst value/threshold ratio is returned and `quality_gates`
says which gate failed (`best_of`).

Both prompts carry `prompt_tokens` / `negative_prompt_tokens` in the sidecar.

`texture2d validate-tileable IMAGE --max-isolated-detail 0.12 --max-banding 0.5
--max-tail-ratio 5` applies the same gates to an existing file (exit 1 above
any threshold).

```bash
# A dark stained pine: grain along V, the palette's colour, no gaps or flecks
texture2d generate "very dark stained pine wood, fine straight vertical grain" \
  --quality high -W 1024 -H 1024 --seed 51121 \
  --max-isolated-detail 0.12 --max-banding 0.5 --max-tail-ratio 5 --attempts 6 \
  --axis v --match-color "#2F2D25" -o pine.png
```

### `texture2d presets`

List all available material presets with their prompts and recommended parameters.

```bash
texture2d presets
```

### `texture2d batch FILE`

Batch-generate textures from a prompts file (one prompt per line, `#` for comments).

```bash
texture2d batch prompts.txt -d textures/ --quality high
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d, --output-dir` | path | `outputs/textures/` | Output directory |
| `-p, --preset` | str | None | Default preset applied to all prompts |
| `-W, --width` | int | 512 | Image width |
| `-H, --height` | int | 512 | Image height |
| `-s, --steps` | int | 30 | Inference steps |
| `-g, --guidance` | float | 7.0 | Guidance scale |
| `-m, --model` | str | None | HF model ID override |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g. `"0,1"`) |
| `--quality` | str | `medium` | Quality tier |
| `--hw-auto/--no-hw-auto` | flag | `on` | Hardware auto-detection |
| `--ground` | str | `auto` | Top-down ground mode |

### `texture2d info`

Display configuration, system info (Python, PyTorch, CUDA, GPUs), HF cache location, and default output path.

```bash
texture2d info
```

### `texture2d skill install`

Install the Cursor Agent Skill (`SKILL.md`) into a game project's `.cursor/skills/texture2d/` directory.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Target project root directory |
| `--force` | flag | `false` | Overwrite existing skill file |

```bash
texture2d skill install -t /path/to/my-game --force
```

### Unified Model Server (vramd)

Prefer **`vramd`** (monorepo supervisor): one socket, smart VRAM
eviction, job queue with priority + affinity. `texture2d generate` auto-delegates
(and can auto-start the vramd unless `VRAMD_AUTO_START=0`).

```bash
vramd start
texture2d generate "stone wall" -o stone.png
texture2d generate "wood" -o wood.png --vramd-stream          # queue/progress events
texture2d generate "dirt" -o dirt.png --vramd-priority batch
texture2d generate "test" -o t.png --no-vramd                 # force in-process
vramd queue
vramd stop
```

| Flag | Description |
|------|-------------|
| `--vramd-priority interactive\|batch` | Queue priority (default interactive / `VRAMD_PRIORITY`) |
| `--no-vramd` | Skip vramd; run in-process |
| `--vramd-stream` | Print vramd queue/progress NDJSON events |

Per-tool `texture2d server` remains only as a **deprecated** fallback. See [`Vramd/README.md`](../Vramd/README.md).

## Quality Presets

The `--quality` flag selects a preconfigured parameter profile. Profiles only fill defaults — explicitly provided flags (`-W`, `-H`, `-s`, `-g`) always take precedence.

| Profile | Resolution | Steps | Guidance | Refine | Description |
|---------|-----------|-------|----------|--------|-------------|
| `fast` | 512×512 | 16 | 7.0 | — | Quick preview, minimum viable quality |
| `low` | 512×512 | 24 | 7.0 | — | Basic quality, faster generation |
| `medium` | 512×512 | 28 | 7.0 | — | Standard quality (**default**) |
| `high` | 768×768 | 28 | 7.0 | 12 | Hires: native 512 + latent upscale + refine |
| `highest` | 1024×1024 | 32 | 7.0 | 16 | Hires: native 512 + latent upscale + refine |

### Material Presets

Each material preset overrides steps and guidance with curated values:

| Preset | Steps | Guidance | Category |
|--------|-------|----------|----------|
| Wood | 50 | 7.5 | Natural |
| Fabric | 50 | 7.5 | Natural |
| Metal | 60 | 8.0 | Industrial |
| Stone | 50 | 7.5 | Natural |
| Brick | 50 | 7.5 | Architectural |
| Leather | 50 | 7.5 | Natural |
| Concrete | 50 | 7.5 | Industrial |
| Marble | 60 | 8.0 | Architectural |
| Grass | 30 | 7.0 | Terrain |
| Sand | 30 | 7.0 | Terrain |
| Dirt | 30 | 7.0 | Terrain |
| Gravel | 30 | 7.0 | Terrain |
| Tile Floor | 30 | 7.0 | Architectural |

```bash
# Use a preset with quality-tier resolution
texture2d generate "scratched surface" --preset Metal --quality high -o metal.png
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXTURE2D_MODEL_ID` | Override default SD model ID (`stable-diffusion-v1-5/stable-diffusion-v1-5`) |
| `TEXTURE2D_VAE_ID` | Override VAE (default `stabilityai/sd-vae-ft-mse`); `none` keeps the checkpoint VAE |
| `TEXTURE2D_HW_AUTO` | Set to `0` to disable hardware auto-detection |
| `TEXTURE2D_BIN` | Override `texture2d` binary path (used by GameAssets) |

## Output Layout

```
outputs/
└── textures/
    ├── rough_stone_wall_surface_medieval_castle_1715000000.png
    └── rough_stone_wall_surface_medieval_castle_1715000000.json
```

- **PNG** — generated seamless texture image.
- **JSON** — metadata sidecar with `seed`, `prompt_final`, generation parameters, model info.
- Default output: `outputs/textures/`. Override with `-o` (generate) or `-d` (batch).

## Pipeline Integration

### Materialize (PBR maps)

Generate a diffuse texture, then use [Materialize](../Materialize/) to create PBR maps (normal, height, metallic, roughness, ambient occlusion):

```bash
texture2d generate "mossy stone" -o diffuse.png
materialize diffuse.png --output-dir pbr/
```

### GameAssets batch

[GameAssets](../GameAssets/) can use `texture2d` as the image source:

- In `game.yaml`, set `image_source: texture2d` (global) or per CSV row.
- With `texture2d.materialize: true` in the profile, GameAssets generates PBR maps automatically via Materialize.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

Use `TEXTURE2D_BIN` if the `texture2d` command is not on `PATH`.

## Development

```bash
cd Texture2D

# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check .

# Format
ruff format .
```

## Project Layout

```
Texture2D/
├── src/texture2d/
│   ├── __init__.py
│   ├── __main__.py            # python -m texture2d
│   ├── _validate_cli.py       # validate-tileable command
│   ├── cli.py                 # Click CLI (generate, batch, presets, server, info, skill)
│   ├── cli_rich.py            # Rich-click integration
│   ├── client.py              # Model server client
│   ├── cursor_skill/
│   │   └── SKILL.md           # Cursor Agent Skill
│   ├── generator.py           # SD1.5 + seamless 2.0 (modes, decode, hires refine, heal)
│   ├── hardware.py            # Hardware auto-detection profile
│   ├── image_processor.py     # Image saving + metadata
│   ├── presets.py             # 13 material presets
│   ├── prompt_budget.py       # CLIP 77-token window: prompt/negative fitting
│   ├── prompt_enhancer.py     # Ground/top-down prompt enhancers
│   ├── server.py              # Model server (keeps pipeline warm)
│   ├── tileability.py         # Tileability helpers
│   └── utils.py               # Helpers (validation, seeds, formatting)
├── config/
│   └── requirements-dev.txt   # Development dependencies
├── scripts/
│   └── installer.py           # System-wide installer
└── tests/
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights (default):** [stable-diffusion-v1-5/stable-diffusion-v1-5](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5) — CreativeML Open RAIL-M license; comply with the model's use restrictions.
- **Full license table:** [AiGameKit/README.md](../README.md) (Licenses section).
