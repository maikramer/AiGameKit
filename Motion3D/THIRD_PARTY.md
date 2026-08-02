# Third-party components — Motion3D

## T2M-GPT (vendored)

- **Source:** [Mael-zys/T2M-GPT](https://github.com/Mael-zys/T2M-GPT)
- **Location:** `src/motion3d/vendor/t2mgpt/`
- **License:** Apache 2.0 — see `src/motion3d/vendor/t2mgpt/LICENSE`
- **Files:** `HumanVQVAE`, `Text2Motion_Transformer`, motion recovery utils (`recover_from_ric`)

Imports were rewritten to package-relative paths under `motion3d.vendor.t2mgpt`.

## Motius weights

- **Primary Hub:** `ZeyuLing/Motius-T2M-GPT-HumanML3D`
- **Fallback Hub:** `ZeyuLing/hftrainer-t2mgpt-humanml3d`
- **Artifacts:** `vq.safetensors`, `gpt.safetensors`, optional `clip.safetensors`, `t2mgpt_config.json`, `Mean.npy`, `Std.npy`

## CLIP text encoder

Phase 1 resolution order:

1. **Optional OpenAI CLIP** (`pip install -e ".[clip]"`): load `ViT-B/32` architecture and apply Motius `clip.safetensors` with `strict=False`.
2. **Fallback:** Hugging Face `transformers` `CLIPTextModel` + `CLIPTokenizer` from `openai/clip-vit-base-patch32`. If Motius keys partially match, they are loaded with `strict=False`; otherwise the pretrained HF text tower is used.

This matches Motius artifact design (frozen ViT-B/32 text tower bundled as safetensors).

## HumanML3D normalization

`Mean.npy` / `Std.npy` travel with the Motius checkpoint (263-dim HumanML3D training stats).
