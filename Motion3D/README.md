# Motion3D

Text-to-motion CLI for AiGameKit using **Motius T2M-GPT HumanML3D** (VQ-VAE + GPT + CLIP).

Patterns: **Text3D** (UMS / QualityEngine / worker) + **Animator3D** (retarget onto SkinTokens) + Shared `save_glb`.

## Install

```bash
./install.sh motion3d
```

Creates `Motion3D/.venv`, installs PyTorch + `bpy`, links Shared + **Animator3D** (`cross_deps`), and the `[dev]` extra (pytest). CLI lands on `PATH` as `motion3d`.

Always run from that venv (`make test-motion3d`, or the `motion3d` wrapper). Do not borrow Text3D’s venv.

```bash
motion3d doctor
```

## Happy path (skinned walk)

```bash
# 1) GPU — NPZ with HumanML3D joints
motion3d generate "a person walks forward" -o walk.npz

# 2) CPU — bake onto a SkinTokens *_rigged.glb (Animator3D retarget)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb --clip walk --in-place
```

`--in-place` (default) strips horizontal travel + yaw drift so the clip loops under game locomotion. Use `--root-motion` only when the clip itself should carry displacement.

Optional intermediate HML22 source GLB (debug / Animator3D CLI):

```bash
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb \
  --keep-source hml22_source.glb
# or:
motion3d export-glb walk.npz -o hml22_source.glb --in-place
animator3d retarget hero_rigged.glb hml22_source.glb out.glb \
  --profile hml22 --source-track t2m_motion --clip walk
```

## Outputs

| Artifact | Contents |
|----------|----------|
| `.npz` | `hml263 (T,263)`, `joints (T,22,3)` Y-up meters, `fps=20`, `prompt`, `n_frames` |
| `export-glb` / `generate … .glb` | **HML22 source** armature (SkinTokens names, look-at bake) — not skinned mesh |
| `apply-rigged` | Target mesh + retargeted clip (deliverable for VibeGame / game-pack) |

`generate -o walk.glb` writes the HML22 source only. For a playable hero, always finish with `apply-rigged`.

## Pipeline (what each stage owns)

```
prompt → Motius T2M-GPT → joints (Y-up)
       → bpy_export (Z-up, swing-only look-at, neutral A-pose, leaf rest, foot rest from target)
       → Animator3D retarget --profile hml22
       → skinned GLB
```

Hard lessons (aim map, leaf bones, feet vs arms, leg splay):  
[`docs/findings/MOTION3D_FINDINGS.md`](../docs/findings/MOTION3D_FINDINGS.md) ·  
[`docs/findings/ANIMATOR_RETARGET_FINDINGS.md`](../docs/findings/ANIMATOR_RETARGET_FINDINGS.md).

## UMS

```bash
motion3d generate "…" -o out.npz          # delegates to UMS by default
motion3d generate "…" -o out.npz --no-ums # in-process
motion3d serve --ums-worker               # subprocess worker
```

Backend: `motion3d` in ModelServer `backends.yaml` (`footprint_key: motius-t2mgpt`).

GameAssets: `run_motion3d_wave_or_fallback` / `motion3d_specs_from_items` in `ums_batch.py` (NPZ/GLB generate wave). Skinned bake stays a CPU `apply-rigged` step after the wave.

## Quality

`--quality fast|low|medium|high|highest` soft-fills `max_frames` / `temperature` via QualityEngine.

## Weights

HF: [`ZeyuLing/Motius-T2M-GPT-HumanML3D`](https://huggingface.co/ZeyuLing/Motius-T2M-GPT-HumanML3D)

Cache: `~/.cache/aigamekit/models/motius-t2mgpt-humanml3d`

## Vendor

Apache-2.0 T2M-GPT core under `src/motion3d/vendor/t2mgpt/` — see [THIRD_PARTY.md](THIRD_PARTY.md).

## Tests

```bash
make test-motion3d   # Motion3D/.venv
```

Agent notes: [AGENTS.md](AGENTS.md). PT: [README_PT.md](README_PT.md).
