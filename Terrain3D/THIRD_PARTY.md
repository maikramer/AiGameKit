# Third-Party Software

This package includes vendored code from the following projects:

## terrain-diffusion

- **Source:** https://github.com/xandergos/terrain-diffusion
- **License:** MIT License (Copyright © 2025 Alexander Goslin)
- **Files:** `src/terrain3d/vendor/` (inference, models, scheduler, data, common)
- **Modifications:** Import paths changed from `terrain_diffusion.*` to `terrain3d.vendor.*`. Training, evaluation, ONNX, explorer, API, and Minecraft code excluded.

## Hugging Face Model Weights

- **xandergos/terrain-diffusion-30m** — https://huggingface.co/xandergos/terrain-diffusion-30m
- **xandergos/terrain-diffusion-90m** — https://huggingface.co/xandergos/terrain-diffusion-90m

Model weights are downloaded at runtime from Hugging Face Hub. Check each model card for license and usage terms.
