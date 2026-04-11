# Text2Sound

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

CLI for stereo **44.1 kHz** audio from text prompts, with two Hugging Face checkpoints (same `stable-audio-tools` library):

| Use | Model | Max duration | Command |
|-----|-------|--------------|---------|
| Music / long ambience | [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) | ~47 s | `--profile music` (default) |
| Short effects / SFX | [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) | ~11 s | `--profile effects` |

Both repos can be **gated**: accept terms on the Hub and set `HF_TOKEN`. **Stability AI Community License** (`LICENSE.md` in each repo): research and non-commercial use; commercial use with **revenue cap** (see current text in the repo and [stability.ai/license](https://stability.ai/license)). Summary: [GameDev/README.md — Licenses](../README.md).

## Features

- **Two profiles** — `music` (Open 1.0, up to ~47 s) and `effects` (Open Small, up to ~11 s; defaults ~8 steps, CFG 1.0, `pingpong` sampler)
- **Text-to-audio** — stereo audio per chosen model
- **Game-dev presets** — ambient, battle, menu, footsteps, weather, UI, nature, dungeon, tavern, etc.
- **Multiple formats** — WAV, FLAC, OGG
- **Batch** — many audios from a prompt file
- **Seed** — full reproducibility
- **Auto trim** — leading and trailing silence removal
- **JSON metadata** — generation params saved next to audio (includes `seed_effective`, sigmas, trim, CLI version)
- **VRAM management** — automatic cleanup after each generation

## Requirements

- Python 3.10+
- PyTorch 2.1+ (CUDA recommended)
- ~4 GB VRAM (GPU generation)
- HF token (if the model requires auth): `HF_TOKEN`

## Installation

### Official (monorepo)

At the **GameDev** repo root:

```bash
cd /path/to/GameDev
./install.sh text2sound
```

General guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development

```bash
cd Text2Sound
bash scripts/setup.sh
source .venv/bin/activate
```

### Local shortcut

```bash
python3 scripts/installer.py --use-venv
```

## Usage

### Generate audio

```bash
text2sound generate "ocean waves crashing on a sandy beach at sunset"
text2sound generate "epic orchestral battle music" --duration 45 --steps 120
text2sound generate "short alien laser shot" --profile effects -d 1.5
text2sound generate "footsteps on gravel" -d 5 -s 80 --format flac
text2sound generate "rain and thunder" --seed 42 --cfg-scale 8
```

### Model and aliases

- **`--profile music`** (default): `stabilityai/stable-audio-open-1.0`
- **`--profile effects`**: `stabilityai/stable-audio-open-small`
- **`--model`** overrides profile: full HF ID or aliases `music`, `full`, `effects`, `small`, `sfx`

```bash
text2sound generate "loop" --model small -d 8
text2sound generate "score" --model music -d 30
```

### Presets

```bash
text2sound presets                          # list presets
text2sound generate --preset battle ""      # preset only
text2sound generate --preset ambient "with gentle river flowing"  # preset + custom
```

### Batch

Output directory uses **`-O` / `--output-dir`** (in `generate`, `-d` is always **duration**).

```bash
# prompts.txt (one prompt per line, # = comment)
text2sound batch prompts.txt --format flac -O sounds/
text2sound batch prompts.txt --seed 1000   # seeds 1000, 1001, 1002, … per line
```

### Info

```bash
text2sound info     # environment, GPU, model, config
text2sound --help   # full help
```

## Available presets

| Preset | Type | Duration |
|--------|------|----------|
| ambient | Calm ambience | 45s |
| battle | Combat music | 30s |
| menu | Menu music | 30s |
| footsteps-stone | Footsteps on stone | 5s |
| footsteps-grass | Footsteps on grass | 5s |
| rain | Rain with thunder | 45s |
| wind | Strong wind | 30s |
| thunder | Isolated thunder | 8s |
| ui-click | UI click | 2s |
| ui-confirm | Confirmation | 3s |
| forest | Forest | 45s |
| ocean | Ocean waves | 45s |
| dungeon | Dark dungeon | 30s |
| tavern | Medieval tavern | 30s |
| explosion | Explosion | 5s |
| sword-clash | Swords | 3s |
| magic-spell | Magic | 4s |
| victory-fanfare | Victory fanfare | 8s |

## Advanced parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--duration` | 30 | Duration in seconds (max ~47 music, ~11 effects; min 0.5) |
| `--steps` | 100 | Diffusion steps (8–150; effects ~8) |
| `--cfg-scale` | 7.0 | Classifier-free guidance (1–15) |
| `--sigma-min` | 0.3 | Min noise schedule |
| `--sigma-max` | 500 | Max noise schedule |
| `--sampler` | dpmpp-3m-sde | Sampler type |
| `--seed` | random | Reproducibility |
| `--trim/--no-trim` | trim | Remove leading and trailing silence |

## Layout

```
Text2Sound/
├── src/text2sound/
│   ├── cli.py             # Click CLI (generate, batch, presets, info)
│   ├── generator.py       # Stable Audio Open pipeline
│   ├── presets.py         # Game-dev audio presets
│   ├── audio_processor.py # Audio processing (trim, etc.)
│   └── utils.py           # Helpers
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh           # Venv + deps
│   └── installer.py       # Standalone installer
└── tests/
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face token (or `HUGGINGFACEHUB_API_TOKEN`) |
| `HF_HOME` | Hugging Face cache (default: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA allocator config (auto-set if empty) |

## GameAssets integration

[GameAssets](../GameAssets/) can call `text2sound` automatically during a batch:

1. In `manifest.csv`, add column **`generate_audio`** with `true` on desired rows.
2. In `game.yaml`, configure the **`text2sound`** block (duration, steps, format, etc.).
3. Run `gameassets batch` — audio is generated after the 2D image for each row.

```bash
gameassets batch --profile game.yaml --manifest manifest.csv
```

Use `TEXT2SOUND_BIN` if the command is not on `PATH`.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
# Exclude slow tests (load model/GPU):
pytest tests/ -v -m "not slow"
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights:** [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) and [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) — **Stability AI Community License** (accept on Hub; commercial terms and revenue limits in each repo’s `LICENSE.md` and [stability.ai/license](https://stability.ai/license)).
