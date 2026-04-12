Baseline meshes (Hunyuan3D export + variantes de pipeline)
=========================================================

Estes ficheiros são gerados localmente; não estão no repositório por defeito.

Se já tens os GLBs cruos (baseline_XX_*.glb) noutra máquina ou de uma geração
anterior, não precisas de voltar a correr o Hunyuan — só aplica o pós-processo:

  cd Text3D
  ./scripts/apply_baseline_repair_all.sh

Para gerar do zero (Text2D + Hunyuan + PNG) e depois aplicar repair/full — **8** stems
stress-test (rock, tree, crate, sword, pillar, teapot+tampa fina, balde aberto, balança):

  cd Text3D
  ./scripts/generate_baseline_raw_meshes.sh --clean

Por cada stem (ex.: baseline_01_rock):

  baseline_01_rock.glb           — mesh crua Hunyuan (sem repair, sem cortar placas)
  baseline_01_rock_repaired.glb  — mesmo ficheiro + repair_mesh (defaults CLI, incl. remesh)
  baseline_01_rock_full.glb      — repaired + remove_backing_plates (fluxo completo)

Flags na geração do .glb cru:
  --no-mesh-repair     (sem prepare_mesh_topology / repair_mesh / watertight / etc.)
  --no-remove-plates   (mantém backing plates para testes)
  --preset fast
  --max-retries 1
  --save-reference-image   (PNG Text2D junto a cada GLB)

O pós-processo (repaired + full) é scripts/baseline_apply_repair.py (pode correr só
este script sobre GLBs cruos já existentes).

Documentação de técnicas (implementação futura): docs/mesh_optimization_techniques.txt
