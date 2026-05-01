# Text2Sound — AI Text-to-Audio Generation

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for stereo **44.1 kHz** audio from text prompts using [Stable Audio Open](https://huggingface.co/stabilityai/stable-audio-open-1.0). Two Hugging Face checkpoints with automatic quality presets, game-dev–focused presets, and full pipeline integration.

| Profile | Model | Max Duration | Use Case |
|---------|-------|-------------|----------|
| `music` (default) | [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) | ~47 s | Music, long ambience |
| `effects` | [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) | ~11 s | Short SFX, sound effects |

Both models are **gated**: accept terms on the Hub and set `HF_TOKEN`. See [License](#license) for details.

## Overview

- **Two profiles** — `music` (Open 1.0, up to ~47 s) and `effects` (Open Small, up to ~11 s)
- **Quality presets** — 5 tiers (`fast` → `highest`) via QualityEngine, plus 40+ game-dev audio presets
- **Multiple formats** — WAV, FLAC, OGG output
- **Batch generation** — one prompt per line, auto-incremented seeds
- **Auto trim** — leading/trailing silence removal with configurable buffer and threshold
- **JSON metadata** — generation params saved alongside audio (`seed_effective`, sigmas, trim, version)
- **VRAM management** — automatic cleanup, low-VRAM mode, multi-GPU support
- **Reproducibility** — full seed control

## Installation

### Monorepo (recommended)

```bash
cd Shared && pip install -e .
cd Text2Sound && pip install -e .
```

Or use the unified installer:

```bash
./install.sh text2sound
```

### Requirements

- Python 3.10+
- PyTorch 2.1+ with CUDA (~4 GB VRAM minimum)
- HF token (for gated models): set `HF_TOKEN` environment variable

## Commands

Entry point: `text2sound` or `python -m text2sound`

Global flag: `--verbose` / `-v` — enable detailed logs.

### `text2sound generate PROMPT`

Generate audio from a text prompt.

```bash
# Basic generation (music profile, 30s)
text2sound generate "ocean waves crashing on a sandy beach at sunset"

# Long music with custom settings
text2sound generate "epic orchestral battle music" -d 45 -s 120 --cfg-scale 8

# Short SFX with effects profile
text2sound generate "short alien laser shot" --profile effects -d 1.5

# Using a game-dev preset
text2sound generate --preset battle ""
text2sound generate --preset ambient "with gentle river flowing"

# FLAC output with trim and seed
text2sound generate "footsteps on gravel" -d 5 -s 80 -f flac --seed 42 --trim
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PROMPT` | str | required | Text description of the audio to generate |
| `--profile` | choice | `music` | Audio profile: `music` or `effects` |
| `-o, --output` | path | `outputs/audio/<name>.wav` | Output file path |
| `-d, --duration` | float | 30.0 | Duration in seconds (0.5–47 music, 0.5–11 effects) |
| `-s, --steps` | int | 100 | Diffusion steps (8–150) |
| `-c, --cfg-scale` | float | 7.0 | Classifier-free guidance (1.0–15.0) |
| `--seed` | int | random | Reproducible seed |
| `-f, --format` | choice | `wav` | Output format: `wav`, `flac`, or `ogg` |
| `-p, --preset` | choice | None | Game-dev preset name (see [Presets](#available-presets)) |
| `--sigma-min` | float | 0.3 | Min noise schedule |
| `--sigma-max` | float | 500.0 | Max noise schedule |
| `--sampler` | str | `dpmpp-3m-sde` | Sampler type |
| `--trim/--no-trim` | flag | trim | Remove leading/trailing silence |
| `-m, --model` | str | None | Model override: HF ID or alias (`music`, `full`, `effects`, `small`, `sfx`) |
| `--half/--no-half` | flag | auto | Half precision (auto-enabled on ≤8 GB VRAM) |
| `--low-vram` | flag | false | Low VRAM mode (auto float16, reduced settings) |
| `--gpu-ids` | str | None | GPU IDs for multi-GPU split (e.g., `0,1`) |
| `--profiler` | flag | false | Record performance metrics (perf DB + JSONL) |
| `--quality` | choice | `medium` | Quality tier (resolves params via QualityEngine) |
| `--category` | str | None | Asset category for automatic audio tuning (e.g., `weapon`, `humanoid`) |

**Model defaults by profile:**

| Profile | Steps | CFG Scale | Sampler | Max Duration |
|---------|-------|-----------|---------|-------------|
| `music` | 100 | 7.0 | `dpmpp-3m-sde` | 47 s |
| `effects` | 8 | 1.0 | `euler` | 11 s |

### `text2sound batch FILE`

Batch generate audio from a prompts file (one prompt per line, `#` for comments).

```bash
# prompts.txt: one prompt per line
text2sound batch prompts.txt -O sounds/
text2sound batch prompts.txt --format flac --seed 1000  # seeds 1000, 1001, 1002…
text2sound batch prompts.txt --profile effects --preset explosion
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `FILE` | path | required | Prompts file (one per line, `#` = comment) |
| `-O, --output-dir` | path | `outputs/audio/` | Output directory |
| `--seed` | int | random | Base seed (incremented per row) |
| `--profile` | choice | `music` | Audio profile |
| `-p, --preset` | str | None | Preset applied to all prompts |
| (remaining flags) | | | Same as `generate` (duration, steps, cfg-scale, format, trim, model, etc.) |

### `text2sound presets`

List all available game-dev audio presets with their parameters.

```bash
text2sound presets
```

### `text2sound info`

Show configuration, environment, GPU, and model info.

```bash
text2sound info
```

### `text2sound skill install`

Install the Cursor agent skill into a game project.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-t, --target` | path | `.` | Target project directory (creates `.cursor/skills/text2sound/`) |
| `--force` | flag | false | Overwrite existing SKILL.md |

```bash
text2sound skill install -t /path/to/game --force
```

## Quality Presets

The `--quality` flag resolves optimal generation parameters via [QualityEngine](../Shared/). CLI parameters always win over quality defaults — quality only fills in values you didn't explicitly set.

```bash
text2sound generate "rain on a tin roof" --quality high
text2sound generate "sword slash" --quality fast --category weapon
```

| Tier | Steps | CFG Scale | σ_min | Sampler | Description |
|------|-------|-----------|-------|---------|-------------|
| `fast` | 12 | 4.0 | 0.5 | `dpmpp-3m-sde` | Minimum viable quality |
| `low` | 20 | 5.0 | 0.4 | `dpmpp-3m-sde` | Basic quality |
| `medium` | 32 | 6.0 | 0.3 | `dpmpp-3m-sde` | Standard (default) |
| `high` | 50 | 7.0 | 0.2 | `dpmpp-3m-sde` | High quality |
| `highest` | 100 | 7.0 | 0.1 | `dpmpp-3m-sde` | Maximum quality |

## Available Presets

40+ game-dev presets organized by category. Use with `--preset NAME`.

### Ambiences

| Preset | Duration | Steps | CFG |
|--------|----------|-------|-----|
| `ambient` | 45s | 100 | 6.0 |
| `forest` | 45s | 100 | 6.0 |
| `ocean` | 45s | 100 | 6.0 |
| `rain` | 45s | 100 | 6.0 |
| `wind` | 30s | 100 | 6.0 |
| `dungeon` | 30s | 110 | 7.0 |
| `tavern` | 30s | 110 | 6.0 |
| `cave` | 30s | 100 | 6.5 |
| `city` | 30s | 100 | 6.0 |
| `desert` | 30s | 100 | 6.0 |
| `space` | 30s | 100 | 6.5 |
| `underwater` | 30s | 100 | 6.0 |

### Music

| Preset | Duration | Steps | CFG |
|--------|----------|-------|-----|
| `battle` | 30s | 120 | 7.0 |
| `menu` | 30s | 100 | 7.0 |
| `victory` | 8s | 100 | 7.5 |
| `defeat` | 8s | 100 | 7.0 |
| `exploration` | 30s | 100 | 7.0 |
| `boss` | 30s | 120 | 8.0 |

### SFX — Impact / Magic / Movement / UI / Creature / Destruction / Weapon / Mechanical / Elemental / Vocal / Collectible / Alarm

| Preset | Category | Duration | Steps | CFG |
|--------|----------|----------|-------|-----|
| `explosion` | Impact | 5s | 80 | 9.0 |
| `sword-clash` | Impact | 2s | 80 | 9.0 |
| `punch` | Impact | 1.5s | 80 | 9.0 |
| `gunshot` | Impact | 1s | 80 | 9.0 |
| `arrow` | Impact | 1.5s | 80 | 9.0 |
| `magic-spell` | Magic | 3s | 90 | 9.0 |
| `heal` | Magic | 2s | 90 | 9.0 |
| `teleport` | Magic | 2s | 90 | 9.0 |
| `shield` | Magic | 2s | 90 | 9.0 |
| `footsteps-stone` | Movement | 4s | 80 | 8.0 |
| `footsteps-grass` | Movement | 4s | 80 | 8.0 |
| `footsteps-wood` | Movement | 4s | 80 | 8.0 |
| `footsteps-water` | Movement | 4s | 80 | 8.0 |
| `ui-click` | UI | 1s | 60 | 10.0 |
| `ui-confirm` | UI | 1.5s | 60 | 10.0 |
| `ui-cancel` | UI | 1s | 60 | 10.0 |
| `ui-hover` | UI | 0.5s | 60 | 10.0 |
| `creature-growl` | Creature | 3s | 90 | 9.0 |
| `creature-roar` | Creature | 3s | 90 | 9.0 |
| `creature-death` | Creature | 3s | 90 | 9.0 |
| `glass-break` | Destruction | 1.5s | 80 | 9.0 |
| `wood-break` | Destruction | 2s | 80 | 9.0 |
| `stone-crumble` | Destruction | 3s | 80 | 9.0 |
| `sword-draw` | Weapon | 1.5s | 80 | 8.5 |
| `bow-draw` | Weapon | 2s | 80 | 8.5 |
| `weapon-reload` | Weapon | 2s | 80 | 8.5 |
| `door-open` | Mechanical | 2s | 80 | 8.0 |
| `door-close` | Mechanical | 2s | 80 | 8.0 |
| `lever` | Mechanical | 1.5s | 80 | 8.0 |
| `clockwork` | Mechanical | 3s | 80 | 8.0 |
| `fire-crackle` | Elemental | 4s | 80 | 8.0 |
| `water-splash` | Elemental | 2s | 80 | 8.0 |
| `electricity-zap` | Elemental | 1.5s | 80 | 8.0 |
| `grunt-effort` | Vocal | 1s | 90 | 9.0 |
| `battle-cry` | Vocal | 2s | 90 | 9.0 |
| `death-scream` | Vocal | 2s | 90 | 9.0 |
| `coin-pickup` | Collectible | 1s | 60 | 10.0 |
| `gem-collect` | Collectible | 1.5s | 60 | 10.0 |
| `item-drop` | Collectible | 1.5s | 60 | 10.0 |
| `alarm-klaxon` | Alarm | 3s | 80 | 9.0 |
| `bell-toll` | Alarm | 4s | 80 | 9.0 |
| `thunder-clap` | Ambient SFX | 4s | 80 | 8.0 |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `TEXT2SOUND_BIN` | Override `text2sound` binary path (used by GameAssets) |
| `HF_TOKEN` | Hugging Face token for gated models (or `HUGGINGFACEHUB_API_TOKEN`) |
| `HF_HOME` | Hugging Face cache directory (default: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator config (auto-set if empty) |

## Output Layout

Audio files are saved as WAV (default), FLAC, or OGG. Each file is accompanied by a JSON metadata sidecar with all generation parameters.

```
outputs/audio/
├── ocean_waves_crashing_on_a_sandy_beach_at_sunset.wav
├── ocean_waves_crashing_on_a_sandy_beach_at_sunset.json
├── epic_orchestral_battle_music.wav
└── epic_orchestral_battle_music.json
```

## Pipeline Integration

Text2Sound generates audio for game assets. In the [GameAssets](../GameAssets/) batch pipeline:

1. In `manifest.csv`, add column **`generate_audio`** with `true` on desired rows.
2. In `game.yaml`, configure the **`text2sound`** block (duration, steps, format, etc.).
3. Run `gameassets batch` — audio is generated after the 2D image for each row.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

The `handoff` command can convert WAV → OGG with configurable sample rates (22050 for SFX, 44100 for BGM). Use `--trim` to remove silence latency and avoid perceptible delay in audio playback.

## Development

```bash
cd Text2Sound && pip install -e ".[dev]"
pytest tests/ -v
pytest tests/ -v -m "not slow"   # exclude slow (model/GPU) tests
ruff check .
ruff format .
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights:** [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) and [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) — **Stability AI Community License** (accept terms on Hub; commercial use with revenue cap — see each repo's `LICENSE.md` and [stability.ai/license](https://stability.ai/license)). Summary: [GameDev/README.md — Licenses](../README.md).
