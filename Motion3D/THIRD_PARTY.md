# Third-party components — Motion3D

## HY-Motion-1.0 (vendored)

- **Source:** [Tencent/HY-Motion-1.0](https://github.com/Tencent-Hunyuan/HY-Motion-1.0)
- **Location:** `src/motion3d/vendor/hymotion/` (+ `vendor/hymotion_assets/stats/`)
- **License:** Tencent HY-Motion 1.0 Community License — see `src/motion3d/vendor/hymotion/LICENSE.txt`
- **Notes:** Happy path uses `output_format=dict` / `keypoints3d` only — **no Autodesk FBX SDK**. Prompt-engineering LLM rewriter is **off** by default (`disable_prompt_engineering=True`).

Imports resolve as top-level `hymotion` via `motion3d.vendor_bootstrap.ensure_hymotion_on_path()`.

## Weights (Hugging Face)

- **Hub:** [`tencent/HY-Motion-1.0`](https://huggingface.co/tencent/HY-Motion-1.0)
- **Variants:** `HY-Motion-1.0-Lite/*` (default) · `HY-Motion-1.0/*` (Full)
- **Cache:** `~/.cache/aigamekit/models/hy-motion-1.0/`
- **Text encoders:** CLIP ViT-L/14 (`openai/clip-vit-large-patch14`) + Qwen3-8B (`Qwen/Qwen3-8B`) via `USE_HF_MODELS=1` / optional cache under `encoders/`

## Retarget / export

- Animator3D profile `hml22` (22 SMPL joints = HY keypoints order)
- `bpy` GLB export (SkinTokens bone names) — AiGameKit stack
