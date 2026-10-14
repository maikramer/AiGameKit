---
name: animator3d
description: Animar rigs com o CLI animator3d (bpy/Blender) do AiGameKit — game-pack (todas as animações num comando, retarget Quaternius CC0 para humanoides, procedural para criaturas), catálogo list-animations, retarget-batch, ik-limits, inspect/list-clips e clips individuais walk/run/jump/fall. Usar quando for preciso adicionar animações a um GLB rigado.
---

# Animator3D — animação (bpy 5.2 LTS, Python 3.13)

## Resolver o binário

```bash
ANIMATOR3D=$(command -v animator3d || echo "$HOME/GitClones/AiGameKit/Animator3D/.venv/bin/animator3d")
```

## Comando principal: game-pack

Gera **todas** as animações de um rig num único comando. Humanoides: retarget do pack Quaternius (CC0) por defeito — o naming canónico dos rigs do pipeline é o do Quaternius, mapeamento directo. Criaturas/rigs sem cobertura caem no caminho procedural.

```bash
animator3d game-pack rigged.glb animated.glb --preset humanoid
animator3d game-pack rigged.glb animated.glb --clips idle,walk,run      # subconjunto
animator3d game-pack scorpion_rigged.glb scorpion.glb --preset creature --facing auto
```

| Flag | Efeito |
|------|--------|
| `--preset humanoid\|creature\|flying` | Conjunto procedural (auto-deteção humanoid vs creature por defeito) |
| `--clips idle,walk,run` | Filtrar clips; catálogo: `list-animations` |
| `--anim-pack quaternius\|quaternius2\|villager\|both\|all` | Packs de retarget (UAL1/UAL2/Kevin Iglesias) |
| `--facing auto\|+x\|-x\|+z\|-z` | Eixo do focinho (gait para trás = forçar o eixo certo) |
| `--procedural` | Força procedural mesmo em humanoides |

## Catálogo e inspeção (sem GPU, sem bpy no caso do list-animations)

```bash
animator3d list-animations --pack both --json    # nomes limpos p/ --clips
animator3d list-clips animated.glb               # clips DENTRO de um GLB (JSON)
animator3d inspect rigged.glb                    # armatures, bones, ações
animator3d inspect-rig rigged.glb                # screenshots bones/heatmap de pesos
animator3d screenshot model.glb -o out/          # multi-ângulo p/ QA
```

## Pós-processamento e clips avulsos

```bash
animator3d ik-limits animated.glb out.glb         # passe IK/limites sem re-gerar clips
animator3d rename-clips in.glb out.glb            # renomear NLA tracks
animator3d retarget target.glb source.glb out.glb        # um clip (TARGET SOURCE OUTPUT)
animator3d retarget-batch target.glb out.glb             # todos os clips de um perfil
animator3d jump rigged.glb clip.glb               # clips avulsos: walk|run|jump|fall INPUT OUTPUT
```

## Regras do monorepo

- Retarget Quaternius: **nunca** compensar eixo/pivô em pós binário do GLB; `root` estático nos pés, fora do bone_map.
- O GLB animado é candidato a **LOD0** do asset (ver skill `asset-pipeline`); a ladder de LODs rigados gera-se com `text3d lod`, não com re-rig.

## Diagnóstico

```bash
animator3d check   # versão Blender/bpy funcional
```

Env `BLENDER_COMMAND` se for preciso apontar outro Blender. Logs: `~/.cache/aigamekit/logs/animator3d-AAAA-MM-DD.log`.
