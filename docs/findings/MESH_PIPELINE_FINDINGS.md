# Mesh / master pipeline — descobertas (pós-modelo)

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Omni shape: [`../OMNI_SHAPE_FINDINGS.md`](../OMNI_SHAPE_FINDINGS.md).  
Pipeline layout: [`../MONOREPO_GAME_PIPELINE.md`](../MONOREPO_GAME_PIPELINE.md).

**Dono de mesh ops:** Text3D apenas. GameAssets orquestra `text3d` /
`rigging3d` / `animator3d` / `gamedev-lab` — sem `bpy`/`trimesh` no GameAssets.

---

## Saída Hunyuan / marching cubes (típico)

- Paredes grossas / duplas, rachas minúsculas, field cheio se bbox má.
- Reparo canónico: `gamedev_shared.mesh_repair` com perfis:

| Perfil | Uso |
|--------|-----|
| `topology_clean` | `topology-fix` / generate clean |
| `pre_decimate_uv` | Antes bake-master / LOD texturado |
| `part_decode` | Part3D |
| `post_voxel` | Após remesh voxel |

Cadeia tip.: sanitize NaN → weld → long_edges/slivers → debris → fill holes →
`make_watertight` (só clean) → shade-smooth.

---

## Master DAG (GameAssets default)

```
1 generate (shape cru, Omni)
2 topology-fix (--export-origin feet|center|none, fill-holes-sides)
3 paint
4 bake-master (LOD0 + normais HI→LO; KTX2/meshopt opcional)
5–7 LOD1 / LOD2 / collision
8 rigging3d transfer-weights (HI → LODs)
9 animate por LOD
10 gamedev-lab check glb --category …
```

- Intermediários (`shape`, `painted`, `rigged_hi`, `clean`, …) → `_intermediate/`.
- Resume **tem** de procurar aí (não regenerar do zero).
- `--legacy-pipeline` força caminho antigo.
- `text3d generate --no-topology-fix` mantém Stage 1 cru.

### Orientação / origem

- Correção Hunyuan → OpenGL (modelo de pé) + origem nos **pés** por omissão.
- Propagar por **todas** as stages. Regressão típica: `_shape` já de barriga
  para cima.
- Rigged GLB herda pivô/orientação do LOD0; esqueleto alinhado **dentro** do
  mesh — sem helpers de debug (icosphere/eixos) no export final.
- `gameassets mesh reorigin-feet` para estáticos; rigged/animados: validar
  rotação root antes de só reorigin.

### Normais / export GLTF

- **Não** `normals_split_custom_set(loop_normals)` → V/Tri≈3, ficheiros
  inchados (ex. goblin_shape 33 MB).
- Usar `shade_smooth` + `auto_smooth_angle`.
- `weld_glb`: nunca engolir excepções — `log.warning`.

### Compressão entregável

- Meshopt: bpy 5.2+ `export_meshopt_compression_enable` (+ `libmeshoptimizer-dev`
  Linux).
- KTX2/UASTC: Node + `@gltf-transform/cli`.
- Sem deps: bake-master fallback gracioso; `gamedev-lab check` pode falhar
  regras `texture_format: ktx2` / `compression: meshopt`.
- Doctor: `text3d doctor`.

### Validação

- `gamedev-lab check glb` + `glb_meta` (parser binário, sem bpy).
- Categories: `lod0|lod1|lod2|rigged|collision` + YAML em
  `GameAssets/.../data/rules/`.
- `--no-bpy-inspect` para CI leve.

### LOD0 terminal

| Pipeline chegou a… | LOD0 deve ser |
|--------------------|---------------|
| animate | GLB animado |
| rig sem animate | rigged |
| só paint / bake | painted / bake-master |

---

## Rig / animate (modelos adjacentes)

- Rigging3D: SkinTokens, Python 3.13 + bpy≥5.2.
- Animator3D: `game-pack`, clips `run`/`jump`/`fall`, preset humanoid.
- Transfer weights: `rigging3d transfer-weights` (não Text3D).
- `text3d lod` preserva armatures/animations.

Pose Omni: T-pose Quaternius para humanoids; A-pose para corpos gordos/músculo
(estica menos). Quad/serpente: T-pose humana **não** ancora bem — ver Omni
findings (semântica).

---

## Checklist pós-shape (antes paint)

1. 3 views — sem planos a régua (clip).
2. Bounds / MB razoáveis (não 300 MB achatado).
3. Semântica OK vs `idea` (lobo≠raposa bípede).
4. Sidecar `.omni.json` coerente com manifest.
5. Origem/orientação visual OK → então topology-fix → paint.

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-19 | Compilado de AGENTS.md + ops master pipeline / Omni batch |
