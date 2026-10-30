# Third-party components — Motion3D

## SprongMotion runtime (vendored)

- **Source:** [SegerEnd/sprongmotion-runtime](https://github.com/SegerEnd/sprongmotion-runtime) — tag `v1.1.0`
  (commit `e11fe9ae`), copied verbatim.
- **Location:** `src/motion3d/vendor/sprongmotion/` (`src/`, `include/`, `LICENSE`, `NOTICE`;
  provenance and hashes in `VENDORED.md`).
- **License:** Apache-2.0.
- **Notes:** C11, zero dependencies. Built out of tree into
  `~/.cache/aigamekit/sprongmotion/<version>-<source-hash>/` with the upstream flags
  (`-ffp-contract=off`, no `-Werror`, no `FAST=1`).

## Model weights

- **Hub:** [`SegerEnd/SprongMotion-14M`](https://huggingface.co/SegerEnd/SprongMotion-14M)
- **File:** `SprongMotion-14M-v1.0-Q8_0.gguf` (33,337,824 bytes)
- **SHA-256:** `68fbd311a0d7d75f60508b35254171583c85880e7e124140457b57ac84c5de9d`
- **License:** Apache-2.0 (weights only; the demo media on the model card is not licensed).
- **Cache:** standard HuggingFace hub cache (`~/.cache/huggingface/hub/models--SegerEnd--SprongMotion-14M/`).
- The model card asks for a credit to Seger Endendijk (segerend.nl) when the model is used in a
  project, application, publication or demonstration.

## Retarget / export

- Animator3D profile `sprong22` (`Animator3D/src/animator3d/data/retarget/sprong22.yaml`).
- `bpy` GLB export (SkinTokens/UE5 bone names) — AiGameKit stack.

## Removed upstream (2026-10-30)

The previous engine (Tencent HY-Motion-1.0, own community license) and its vendored
`hymotion` tree, weight variants (`hy-motion-lite` / `hy-motion-full`, ~5.7 GB) and the
vramd backend `motion3d` were removed with this migration. Old HML22 NPZs are refused at
load with a message pointing at `motion3d generate`.
