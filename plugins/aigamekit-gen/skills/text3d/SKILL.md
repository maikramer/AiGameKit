---
name: text3d
description: Gerar e processar meshes 3D com o CLI text3d (Hunyuan3D-Omni) do AiGameKit — texto/imagem → GLB, topology-fix, LODs, simplify, remesh, collision, finish KTX2/meshopt, bake-master e presets fast/balanced/hq. Usar quando for preciso criar um modelo 3D a partir de texto ou imagem, ou operar mesh (LOD, colisão, compressão) sobre um GLB.
---

# Text3D — texto → mesh (Hunyuan3D-Omni) + operações de mesh

É o **único dono das operações de mesh** do monorepo (LOD, collision, simplify, remesh, topology-fix, bake-master). Orquestração de batch vive no `gameassets`, não duplicar aqui.

## Resolver o binário

```bash
TEXT3D=$(command -v text3d || echo "$HOME/GitClones/AiGameKit/Text3D/.venv/bin/text3d")
```

## Gerar

```bash
# Texto → imagem → mesh (pipeline completo)
text3d generate "um robô futurista" -o robo.glb --preset balanced

# Já tenho a imagem de referência (eye-level 3/4) → só o Hunyuan3D
text3d generate -i ref.png -o mesh.glb

# Controlo de proporções da bbox
text3d generate -i ref.png --control-type bbox --bbox 0.8,0.64,1 -o mesh.glb
```

Presets (`--preset`): `fast` (menos VRAM), `balanced` (default), `hq` (GPU grande). Substituem `--steps`/`--octree-resolution`/`--num-chunks`. `--seed N` fixa Text2D + Hunyuan.

Nunca mexer em `--octree-resolution` sem necessidade — o caminho feliz é `category` + `size_m` no manifest do `gameassets` (faces ≈ 8·10⁴·char_m²).

## Pipeline pós-generate (ordem)

```bash
text3d topology-fix cru.glb -o limpo.glb --engine arrays   # Stage 2: weld, slivers, morph-close (dono do fecho de rachas)
paint3d texture limpo.glb -i ref.png -o pintado.glb        # ver skill paint3d
```

## Operações de mesh

| Comando | Uso |
|---------|-----|
| `simplify in.glb -o out.glb --target-faces 24000` | Decimate COLLAPSE (preferir a `remesh`) |
| `lod model.glb -o ./out --basename prop` | LOD0/1/2 (LOD0=cheio) |
| `collision in.glb -o collision.glb` | Mesh de colisão simplificada |
| `finish in.glb` | Tangents → dedup → KTX2 → meshopt (compressão final) |
| `bake-master` | LOD0 master (decimação + tangents + KTX2 + meshopt) |
| `remesh-textured pintado.glb -o out.glb --target-faces 6000` | Remesh isotrópico com reprojeção de textura |
| `align-plus-z in.glb -o out.glb` | Faces ~+Z para baixo ao chão −Y |
| `split-at-height in.glb` | Parte GLB em plano horizontal (stump + top) |
| `patch-pbr in.glb` | Normal+AO sem re-decimar |
| `fix-ktx2-dfd in.glb` | DFD UASTC single-channel RRR → RGB |
| `convert in.ply -o out.glb` | Conversão PLY/GLB |

## Notas de qualidade

- Marching cubes do Hunyuan traz paredes duplas/rachas → `topology-fix` **sempre** antes de paint/rig (base oca por baixo é OK).
- Re-comprimir um GLB existente: `text3d finish asset.glb`; verificar deps: `text3d doctor`.
- `reorigin-feet` (via `gameassets mesh`) repõe origem nos pés — validar orientação antes de usar em modelos rigged.

## VRAM / vramd

Delegação automática. Se bloquear: `vramd status`/`queue` → `--vramd-stream` para seguir progresso ou `vramd cancel <job_id>`. **Nunca** kill/pkill GPU com vramd ocupado; idle a segurar VRAM → `vramd zero`. Bypass: `--no-vramd`.

## Diagnóstico

```bash
text3d doctor        # ambiente + VRAM
text3d info          # sistema + GPU
text3d gpu-processes # quem ocupa a GPU (quando VRAM exclusiva falha)
```

Logs: `~/.cache/aigamekit/logs/text3d-AAAA-MM-DD.log`.
