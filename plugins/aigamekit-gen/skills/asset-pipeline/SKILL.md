---
name: asset-pipeline
description: Pipeline encadeado do AiGameKit para criar um asset de jogo completo a partir de texto — text2d (referência) → text3d (mesh + topology-fix) → paint3d (textura PBR) → rigging3d (rig) → animator3d (game-pack) → text3d lod/collision → validação, com regras LOD0 e checklist vramd. Usar quando o pedido for um asset completo (modelo texturizado, rigado e animado), não uma ferramenta isolada.
---

# Pipeline AiGameKit — texto → asset de jogo completo

Encadeia as skills `text2d` → `text3d` → `paint3d` → `rigging3d` → `animator3d`. Para lotes/manifests usa a skill `gameassets` (`gameassets batch --profile game.yaml --manifest manifest.yaml --plain`; `resume` corre só as fases pendentes) — este fluxo manual serve para um asset único ou debugging de um estágio. As mesmas operações existem como tools MCP do plugin (`text3d_generate`, `paint3d_texture`, `rigging3d_pipeline`, `animator3d_game_pack`, `gameassets_batch`, …).

## Fluxo (master pipeline Round 3)

```bash
# 0. Sufixos canónicos: _shape → (limpo) → _painted → _rigged → _animated → LODs

# 1. Referência visual (eye-level 3/4, fundo limpo)
text2d generate "goblin guerreiro com lança, vista 3/4 ao nível do olho" -o goblin_ref.png

# 2. Mesh + reparação topológica
text3d generate -i goblin_ref.png -o goblin_shape.glb --preset balanced
text3d topology-fix goblin_shape.glb -o goblin.glb --engine arrays

# 3. Textura PBR (albedo + normal + AO)
paint3d texture goblin.glb -i goblin_ref.png -o goblin_painted.glb

# 4. Rig (um passo: skeleton + skin)
rigging3d pipeline -i goblin_painted.glb -o goblin_rigged.glb --quality medium

# 5. Animações (game-pack: retarget Quaternius p/ humanoides)
animator3d game-pack goblin_rigged.glb -o goblin_animated.glb --preset humanoid

# 6. Ladder de LODs RIGADA + colisão + compressão final
text3d lod goblin_animated.glb -o ./out --basename goblin
text3d collision goblin_animated.glb -o goblin_collision.glb
text3d finish out/goblin_lod0.glb
```

Nota: steps 2–3 podem ser um só `text3d generate "prompt" -o …` (gera a referência internamente); separar quando se quer iterar a referência.

## Regra LOD0 (o entregável)

O **LOD0 é o asset final**: animated > rigged > painted, por essa ordem de preferência.

- Tem animação → o GLB animado é o LOD0.
- Rig sem animação → o rigged é o LOD0.
- Só chega ao paint → LOD0 decimado do painted (~1.2× `target_faces` da categoria, via `text3d lod`/`bake-master`).
- LOD0 sem rig/animação quando o asset os tem = **regressão**.

O `text3d lod` preserva armatures/animações — é ele que gera a ladder (decimate sobre o animated/rigged). `rigging3d transfer-weights` é rebinding manual pontual, fora do DAG.

## Validação (QA)

```bash
aigamekit-lab check glb out/goblin_lod0.glb                       # regras por categoria (CI-ready)
aigamekit-lab debug screenshot out/goblin_lod0.glb -o qa/         # multi-ângulo
aigamekit-lab debug inspect out/goblin_lod0.glb                   # JSON: mesh/armature/anims
```

Verificar no GLB final: origem nos pés (y=0), em pé, PBR completo (não só albedo), V/Tri saudável (<~1, sem verts duplicados), clips com nomes do catálogo.

## Checklist vramd (transversal a todos os estágios GPU)

1. Delegação automática — não planear VRAM, não usar `--low-vram`/`--memory-efficient`.
2. `vramd status` / `queue` antes de intervir; `--vramd-stream` para seguir progresso; `vramd cancel <job_id>` para desistir.
3. **Nunca** `kill`/`pkill` GPU com vramd ocupado (mata o workload errado).
4. vramd idle a segurar VRAM → `vramd zero`. Editar código de tool → `vramd respawn <backend>`.
5. Bypass intencional: `--no-vramd` + in-process.

## Docs de fundo (no checkout do monorepo)

- `docs/findings/MESH_PIPELINE_FINDINGS.md` — DAG, V/Tri, LODs
- `docs/MANIFEST_AUTHORING.md` — category/size_m/octree
- `docs/GAMEASSETS_UMS_BATCH.md` — batch/waves via vramd
- `docs/ANIMATOR3D_AFTER_RIG.md` — game-pack/retarget
