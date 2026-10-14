---
name: paint3d
description: Texturizar meshes 3D com o CLI paint3d (Hunyuan3D-Paint 2.1 + Materialize PBR) do AiGameKit — albedo, normal+AO, bake bilateral, upscale Real-ESRGAN, e caminhos rápidos CPU (quick, vertex-pbr). Usar quando for preciso dar textura/PBR a um GLB, de preferência com uma imagem de referência alinhada.
---

# Paint3D — texturização 3D (Hunyuan3D-Paint 2.1 → PBR)

## Resolver o binário

```bash
PAINT3D=$(command -v paint3d || echo "$HOME/GitClones/AiGameKit/Paint3D/.venv/bin/paint3d")
```

## Comando principal

```bash
paint3d texture mesh.glb -i ref.png -o pintado.glb
```

`ref.png` deve estar **alinhada com a mesh** (a mesma vista usada no `text3d generate -i`). Defaults já são os bons: `bake-exp 6` (nítido, menos sangramento), `--smooth` bilateral (1 passada), `--pbr-enrich` ON (normal + AO via Materialize).

| Flag | Efeito |
|------|--------|
| `-o out.glb` | GLB de saída com textura embutida |
| `--upscale --upscale-factor 2\|4` | Real-ESRGAN (1024→2048/4096) |
| `--texture-size 4096` | Resolução do atlas UV |
| `--render-size 2048` | Resolução de rasterização do bake |
| `--pbr-preset default\|skin\|floor\|metal\|fabric\|wood\|stone` | Preset do enriquecimento Materialize |

O GLB sai com PBR completo (metallicRoughness + occlusion; ORM R=AO/G=rough/B=met). Se um GLB antigo só tem albedo, re-pintar em vez de patchar.

## Sem GPU / iteração rápida

```bash
paint3d quick mesh.glb --style solid --color "#8a2be2" -o out.glb   # cor sólida, segundos, CPU
paint3d quick mesh.glb --style perlin --seed 0 -o rock.glb          # ruído FBM 3D (tipo pedra)
paint3d vertex-pbr mesh.glb -o out.glb                              # cor por vértice → difuso → Materialize
```

## Regras do monorepo (não regressar)

- **Sem monkeypatches** ao código vendido em `Paint3D/src/paint3d/hy3dpaint/` — editar o vendor limpo.
- Mesh cru do Hunyuan precisa de `text3d topology-fix` antes do paint (`ensure_clean_for_paint` ajuda, mas o dono do fecho é o topology-fix).
- Assets antigos requering re-paint: correr `vramd respawn paint3d` depois de editar código da tool (não restart do supervisor).

## VRAM / vramd

Delegação automática. Se bloquear: `vramd status`/`queue` → `--vramd-stream` ou `vramd cancel <job_id>`. **Nunca** kill/pkill GPU com vramd ocupado; idle a segurar VRAM → `vramd zero`. Bypass: `--no-vramd`. OOM no worker → cancel + `vramd respawn paint3d`.

## Diagnóstico

```bash
paint3d doctor   # PyTorch, CUDA, VRAM, modelos, rasterizador
paint3d info     # sistema + GPU
```

Logs: `~/.cache/aigamekit/logs/paint3d-AAAA-MM-DD.log`.
