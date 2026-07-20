---
name: text2icon
description: Generate game UI icons (health, ammo, settings, inventory items) from text prompts using Sana Sprint 0.6B. Fast (1-4 steps) and supports transparent backgrounds.
---

# text2icon — Text-to-Icon CLI

Generate game UI icons from text prompts using **Sana Sprint 0.6B** (NVlabs/Sana).

## Quick start

```bash
# Opaque icon (RGB)
text2icon generate "red health potion, fantasy game" -o health.png

# Transparent icon (RGBA via rembg)
text2icon generate "sword icon, medieval RPG" -o sword.png --transparent

# Batch from a file (one prompt per line)
text2icon batch icons.txt -d icons/ --transparent --quality medium
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `-o/--output` | `outputs/icons/<slug>_<ts>.png` | Output file |
| `-W/--width` | `512` | Width (multiple of 8) |
| `-H/--height` | `512` | Height (multiple of 8) |
| `-s/--steps` | `2` | Inference steps (Sprint: 1-4) |
| `-g/--guidance` | `4.5` | Guidance scale (CFG) |
| `--seed` | random | Reproducibility seed |
| `-n/--negative-prompt` | `""` | Negative prompt |
| `--transparent/--no-transparent` | off | Remove background (rembg/U2Net) |
| `-m/--model` | Sana_Sprint_0.6B | HF model ID override |
| `--cpu` | off | Force CPU |
| (hw-auto) | auto | CPU offload / quant por VRAM |
| `--gpu-ids` | auto | Multi-GPU split (e.g. `0,1`) |
| `--quality` | `medium` | Quality tier (fast/low/medium/high/highest) |
| `--hw-auto/--no-hw-auto` | on | Hardware auto-detect (env `TEXT2ICON_HW_AUTO=0`) |

## Models

- **Default**: `Efficient-Large-Model/Sana_Sprint_0.6B_1024px_diffusers` (1-4 steps, ~<1s/img)
- **Standard** (higher quality, ~10x slower): `Efficient-Large-Model/Sana_600M_1024px_diffusers` (20 steps)

Override with `-m` or env `TEXT2ICON_MODEL_ID`.

## Integration with GameAssets

`text2icon` is wired into the `gameassets` batch pipeline (scene-level stage, like skymap2d).
Add a `text2icon:` block to `game.yaml`:

```yaml
text2icon:
  prompts:
    - "health potion icon"
    - "mana potion icon"
    - "sword icon"
    - "shield icon"
  transparent: true
  width: 256
  height: 256
```

Then `gameassets batch` generates them and `gameassets handoff` copies them to `public/assets/icons/`.

## Prompt tips

- The generator auto-prepends `"app icon, simple, centered, bold, clean background..."` unless your prompt already mentions "icon"/"logo"/"emblem".
- For transparent backgrounds, keep the subject centered with a clean/solid background for best rembg results.
- Square dimensions (256×256, 512×512, 1024×1024) work best for icons.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `TEXT2ICON_MODEL_ID` | Override default model |
| `TEXT2ICON_BIN` | Path to `text2icon` binary (used by GameAssets) |
| `TEXT2ICON_HW_AUTO` | `0` disables hardware auto-detection |
| `HF_HOME` | Hugging Face cache directory |
