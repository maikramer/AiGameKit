# AGENTS.md — Motion3D (motion3d)

Text-to-motion (Motius T2M-GPT HumanML3D). GPU via UMS. Skinned bake via **Animator3D** retarget (`hml22`).

## OVERVIEW

- Vendor: `src/motion3d/vendor/t2mgpt/` (Apache-2.0) — do not edit except path rewrites.
- Weights: HF Motius safetensors (VQ + GPT + CLIP) + Mean/Std.
- Happy path NPZ: `motion3d generate "…" -o out.npz`
- Happy path skinned: `motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb --in-place`
  (HML22 source → Animator3D `retarget --profile hml22`)
- Mesh/animation I/O: bpy only (`bpy_export.py` → Shared `save_glb`). No trimesh.
- Install: `./install.sh motion3d` → `Motion3D/.venv` + Animator3D PTH + `[dev]`.

Deep dive: [`docs/findings/MOTION3D_FINDINGS.md`](../docs/findings/MOTION3D_FINDINGS.md).

## WHERE TO LOOK

| Task | File |
|------|------|
| CLI | `cli.py` |
| Inference | `pipeline.py`, `generator.py` |
| Weights / HF | `weights.py` |
| HML22 source GLB (Y→Z, look-at, neutrals) | `bpy_export.py` |
| Skinned bake via Animator3D | `apply_rigged.py` |
| Retarget profile | `Animator3D/.../data/retarget/hml22.yaml` |
| UMS payload / worker | `ums_payload.py`, `worker_serve_adapter.py` |
| Hardware peak | `hardware.py` |
| GameAssets wave | `GameAssets/.../ums_batch.py` (`run_motion3d_wave_or_fallback`) |

## ANTI-PATTERNS

- Do not depend on private `motius` Python package — load safetensors ourselves.
- Do not use trimesh for export — bpy + Shared `save_glb`.
- Do not bake joint **locations** onto SkinTokens mesh — deform. Use `apply-rigged`.
- Do not reimplement retarget in Motion3D — call `animator3d.retarget`.
- Do not edit vendored T2M-GPT except import path / device fixes.
- Do not derive the bone→tail aim from `t2m_kinematic_chain` — use `HML22_AIM_CHILD`.
- Do not build source rest straight from `t2m_raw_offsets` — force real T-pose.
- Do not aim leaf bones (`Head`, `hand_*`, `ball_*`) — keep at rest.
- Do not calibrate **arms** from the target SkinTokens rest (T-pose → open arms).
  Soft A-pose = `HML22_NEUTRAL_AIM`; only feet = `HML22_TARGET_REST_BONES`.
- Call `delegate_or_prepare` before `prepare_gpu_exclusive`.
- Do not run tests / CLI from another tool’s venv — `./install.sh motion3d`.

## TESTS

`make test-motion3d` — CPU floor in `tests/test_motion3d_coverage_100.py` (+ apply/export helpers).

Runs from `Motion3D/.venv`. Without it the Makefile warns and falls back to the
system interpreter (missing `torch` / `huggingface_hub` / `bpy`).
