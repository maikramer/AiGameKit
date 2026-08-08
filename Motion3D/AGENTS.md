# Motion3D — agent notes

Text-to-motion (**Tencent HY-Motion-1.0**). GPU via vramd. GLB export via **bpy** (Animator3D / Text3D Shared patterns).

## Hard rules

- Happy path: `motion3d generate` → NPZ `joints` @ 30fps → `motion3d apply-rigged` (`hml22`). Multi-clip: `motion3d pack-rigged … -m name=path.npz` (ACTIONS export).
- Default model Lite in QualityEngine; **hw-auto may upgrade to Full** on ~6GB (staged text-CPU), like Text2D 4B/9B.
- Prompt engineering LLM **off** (`disable_prompt_engineering=True`).
- Vendor: `src/motion3d/vendor/hymotion/` — patch paths/FBX/offload only; no FBX in happy path.
- Weights: HF `tencent/HY-Motion-1.0` → `~/.cache/aigamekit/models/hy-motion-1.0/`.
- After code edits: `vramd respawn motion3d` (not full vramd restart).
- No Motius / `vendor/t2mgpt` — removed.

## VRAM

- Study Text2D `hardware.py` + `plan_offload` for the pattern.
- FOOTPRINTS `hy-motion-lite|full` = DiT-resident (staged); never admit fp16 stacked Qwen+DiT.
- Pipeline: DiT load (+SDNQ) → text encoder on CPU when `offload_text_encoder` → encode → hidden.to(GPU) → generate.

## Docs

[`docs/findings/MOTION3D_FINDINGS.md`](../docs/findings/MOTION3D_FINDINGS.md)
