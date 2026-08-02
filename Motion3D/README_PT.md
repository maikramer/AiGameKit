# Motion3D

CLI text-to-motion do AiGameKit com **Motius T2M-GPT HumanML3D** (VQ-VAE + GPT + CLIP).

Padrões: **Text3D** (UMS / QualityEngine / worker) + **Animator3D** (retarget SkinTokens) + Shared `save_glb`.

## Instalação

```bash
./install.sh motion3d
```

Cria `Motion3D/.venv`, instala PyTorch + `bpy`, liga Shared + **Animator3D** (`cross_deps`) e o extra `[dev]` (pytest). CLI no PATH: `motion3d`.

Corre sempre a partir desse venv (`make test-motion3d` ou o wrapper `motion3d`). Não usar o venv do Text3D.

```bash
motion3d doctor
```

## Happy path (walk com skin)

```bash
# 1) GPU — NPZ com joints HumanML3D
motion3d generate "a person walks forward" -o walk.npz

# 2) CPU — bake no *_rigged.glb SkinTokens (retarget Animator3D)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb --clip walk --in-place
```

`--in-place` (omissão) tira travel horizontal + yaw drift para o clip loopar sob a locomoção do jogo. `--root-motion` só quando o clip deve carregar o deslocamento.

Source HML22 intermédio (debug / CLI Animator3D):

```bash
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb \
  --keep-source hml22_source.glb
```

## Saídas

| Artefacto | Conteúdo |
|-----------|----------|
| `.npz` | `hml263 (T,263)`, `joints (T,22,3)` Y-up metros, `fps=20`, `prompt`, `n_frames` |
| `export-glb` / `generate … .glb` | armature **source HML22** (nomes SkinTokens, bake look-at) — sem mesh skinned |
| `apply-rigged` | mesh alvo + clip retargetado (entregável VibeGame / game-pack) |

`generate -o walk.glb` só escreve o source HML22. Para o hero jogável, acaba sempre com `apply-rigged`.

## Pipeline

```
prompt → Motius T2M-GPT → joints (Y-up)
       → bpy_export (Z-up, swing-only, A-pose neutro, folhas no rest, pés do rest alvo)
       → Animator3D retarget --profile hml22
       → GLB skinned
```

Lições duras: [`docs/findings/MOTION3D_FINDINGS.md`](../docs/findings/MOTION3D_FINDINGS.md).

## UMS

Delegação por omissão; `--no-ums` in-process. Worker: `motion3d serve --ums-worker`.

GameAssets: wave `run_motion3d_wave_or_fallback` em `ums_batch.py` (generate NPZ/GLB). Bake skinned = passo CPU `apply-rigged` depois da wave.

## Qualidade

`--quality` preenche `max_frames` / `temperature` via QualityEngine (soft).

## Pesos

HF: [`ZeyuLing/Motius-T2M-GPT-HumanML3D`](https://huggingface.co/ZeyuLing/Motius-T2M-GPT-HumanML3D)

Cache: `~/.cache/aigamekit/models/motius-t2mgpt-humanml3d`

Vendor T2M-GPT: [THIRD_PARTY.md](THIRD_PARTY.md).

## Testes

```bash
make test-motion3d
```

Notas de agente: [AGENTS.md](AGENTS.md). EN: [README.md](README.md).
