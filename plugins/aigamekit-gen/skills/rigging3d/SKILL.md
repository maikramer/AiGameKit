---
name: rigging3d
description: Auto-rigging com o CLI rigging3d (SkinTokens — skeleton + skin numa passada) do AiGameKit — pipeline -i/-o, seeds, quality tiers, corrigir orientação de bones e transfer-weights para LODs. Usar quando for preciso rigar um GLB texturizado para depois animar com animator3d.
---

# Rigging3D — auto-rigging (SkinTokens: skeleton + skin unificados)

## Resolver o binário

```bash
RIGGING3D=$(command -v rigging3d || echo "$HOME/GitClones/AiGameKit/Rigging3D/.venv/bin/rigging3d")
```

Stack: Python 3.13 + `bpy>=5.2.0` (LTS). Não assumir outro Python/bpy para esta tool.

## Comando principal

```bash
rigging3d pipeline -i pintado.glb -o rigged.glb --quality medium
```

| Flag | Efeito |
|------|--------|
| `-i` / `-o` | GLB de entrada (idealmente `_painted` do paint3d) / saída |
| `--seed N` | Reprodutibilidade (default 123) |
| `--quality fast\|low\|medium\|high\|highest` | Tier (default `medium`) |
| `--use-existing-skeleton` | Entrada já tem skeleton; gerar só o skin |
| `--transfer/--no-transfer` | Reanexar rig à mesh/textura/escala originais (default ON) |
| `--gpu-ids 0,1` | Split multi-GPU |

## Correções pontuais

```bash
# Bones com tail a apontar para o lado (defeito SkinTokens em coxas/braços)
rigging3d fix-bone-orientation rigged.glb -o rigged_fixed.glb   # sem -o, sobrescreve

# Rebinding manual de LODs (fora do DAG do batch — o text3d lod gera a ladder rigada)
rigging3d transfer-weights -s rigged_hi.glb -t lod1.glb -t lod2.glb   # outputs ao lado, sufixo _rigged
```

## Contrato de qualidade do rig (verificar antes de aceitar)

- O GLB rigado **herda origem/pivô/orientação do LOD0**: mesh centrada em X/Z, y=0 na sola dos pés, em pé, esqueleto alinhado e dentro da malha.
- Sem helpers de debug no GLB final (icosferas, eixos visuais).
- Criaturas quadrúpedes de lado → resolver com correção de **root** (facing), não rodar só o mesh.

## VRAM / vramd

Delegação automática. `--hw-auto` (default) deteta hardware e pina a GPU com mais VRAM livre. Se bloquear: `vramd status`/`queue` → `--vramd-stream` ou `vramd cancel <job_id>`. **Nunca** kill/pkill GPU com vramd ocupado; idle → `vramd zero`. Bypass: `--no-vramd`.

## Diagnóstico

Depois do rig, inspecionar com `animator3d inspect rigged.glb` (armatures, bones, ações) ou `animator3d inspect-rig` (screenshots com bones/heatmap de pesos). Logs: `~/.cache/aigamekit/logs/rigging3d-AAAA-MM-DD.log`.
