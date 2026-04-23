# Text2D

**Language:** English · [Português (`README_PT.md`)](README_PT.md)

**Text-to-image** CLI with [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) in **SDNQ** quantization ([Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic)), in the same spirit as Text3D (Click + Rich, `src/`, scripts).

## Requirements

| Item   | Minimum | Notes |
|--------|---------|--------|
| Python | 3.10+   | Tested on 3.10–3.13 |
| GPU    | Optional | NVIDIA + CUDA recommended for reasonable inference |
| VRAM   | ~6 GB+ with `--low-vram` and 512² | Depends on checkpoint; modest GPUs: `--low-vram`. Multi-GPU with `--gpu-ids` splits model across devices |
| Disk   | ~8 GB   | HF cache + SDNQ weights (~2.5 GB on disk) |

**Weight license:** the default is the SDNQ checkpoint [Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic), which on Hugging Face is tied to **FLUX Non-Commercial** (`flux-non-commercial-license` in metadata), **distinct** from the official [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (**Apache 2.0** on the model card). For commercial use with less ambiguity, set `TEXT2D_MODEL_ID=black-forest-labs/FLUX.2-klein-4B` (more VRAM). Summary: [Licenses in the monorepo](../README.md).

## Installation

### Official (monorepo)

At the **GameDev** repo root (folder with `install.sh` and `Shared/`):

```bash
cd /path/to/GameDev
./install.sh text2d
```

Equivalent: `gamedev-install text2d`. General guide: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / development (`scripts/setup.sh`)

`setup.sh` does **not** replace the official installer; it is a convenience to create `Text2D/.venv` and `pip install -e` locally.

```bash
cd Text2D
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
text2d --help
```

- With **NVIDIA**, `setup.sh` installs PyTorch with CUDA (**Python 3.13+** uses **PyPI** wheels; 3.10–3.12 uses `cu121`/`cu118` index).
- Runtime deps: [`config/requirements.txt`](config/requirements.txt). Dev/tests: [`config/requirements-dev.txt`](config/requirements-dev.txt) or `pip install -e ".[dev]"`.

### Local shortcut (`scripts/installer.py`)

With `.venv` already created (e.g. after `setup.sh`):

```bash
chmod +x scripts/run_installer.sh scripts/install.sh
./scripts/run_installer.sh --use-venv --prefix ~/.local
# or: ./scripts/install.sh … (delegates to run_installer.sh)
```

Install from system `python3` (PyTorch + requirements + package + wrappers in `PREFIX/bin`):

```bash
python3 scripts/installer.py --prefix ~/.local
```

Options: `--use-venv`, `--skip-deps`, `--skip-models`, `--force`, `--prefix`, `--python`. Without `.venv` and with `--use-venv`, the installer **exits with error** (create the venv first).

Detailed docs: [docs/INSTALL.md](docs/INSTALL.md). GPU/load issues: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## First run vs later runs

- **First run:** downloads several GB from Hugging Face — can take **many minutes**; GPU may show **0%** during network/disk (normal).
- **With local cache:** the same command often finishes in **seconds to ~1 min** (load from disk + inference), depending on hardware.

## Usage

| Subcommand | Description |
|------------|-------------|
| `text2d generate PROMPT` | Generate an image from text |
| `text2d info` | Show config and environment (GPU, cache, model) |
| `text2d models` | List available models |
| `text2d skill install` | Install Cursor Agent Skill in the project |

```bash
text2d generate "a cat holding a sign that says hello world"

text2d generate "sunset landscape" --width 768 --height 768 --steps 4 --guidance 1.0

text2d generate "portrait" --low-vram -o mine.png --seed 42

# Multi-GPU: split model across GPUs 0 and 1
text2d generate "portrait" --gpu-ids 0,1 -o mine.png

text2d generate "test" -v          # --verbose on this subcommand
text2d -v generate "test"          # or verbose on the group

text2d info
text2d models
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `TEXT2D_MODEL_ID` | Alternative HF repo compatible with `Flux2KleinPipeline` (e.g. `black-forest-labs/FLUX.2-klein-4B` for Apache 2.0; default SDNQ = Disty0 terms) |
| `HF_HOME` | Hugging Face cache (default: `~/.cache/huggingface`) |
| `TEXT2D_MODELS_DIR` | Local models directory; installer writes `~/.config/text2d/config.env` when `Text2D/models/` exists with weights |
| `TEXT2D_OUTPUT_DIR` | Image output directory (installer creates `~/.text2d/outputs`) |
| `PYTORCH_CUDA_ALLOC_CONF` | CUDA config (auto-set if empty) |

### Guidance

The **SDNQ Disty0** checkpoint defaults to **guidance 1.0** (see [model card](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic)). The official BFL BF16 often uses higher values (e.g. 4.0).

## GGUF / Unsloth

**GGUF** weights target **ComfyUI-GGUF** workflows, not this CLI (Diffusers).

## Layout

```
Text2D/
├── src/text2d/
│   ├── cli.py             # Click CLI (generate, info, models)
│   ├── generator.py       # FLUX pipeline + inference
│   ├── cli_rich.py        # Rich config for CLI
│   └── utils/             # Helpers (paths, etc.)
├── docs/
│   ├── INSTALL.md         # Detailed install guide
│   └── TROUBLESHOOTING.md # Troubleshooting
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh           # Venv + deps setup
│   ├── run_installer.sh   # Calls installer.py (implementation)
│   ├── install.sh         # Delegates to run_installer.sh (local shortcut)
│   └── installer.py       # Logic shared with gamedev-install
└── tests/
```

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

- **Code:** MIT — [LICENSE](LICENSE).
- **Weights:** default SDNQ follows [Disty0 card](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) (non-commercial in HF metadata). BFL BF16 checkpoint: [FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (Apache 2.0). Full table: [GameDev/README.md — Licenses](../README.md).
