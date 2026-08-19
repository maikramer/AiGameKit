# Mesh / master pipeline — descobertas (pós-modelo)

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Omni shape: [`../OMNI_SHAPE_FINDINGS.md`](../OMNI_SHAPE_FINDINGS.md).  
Pipeline layout: [`../MONOREPO_GAME_PIPELINE.md`](../MONOREPO_GAME_PIPELINE.md).  
Animate / Quaternius: [`ANIMATOR_RETARGET_FINDINGS.md`](ANIMATOR_RETARGET_FINDINGS.md).

**Dono de mesh ops:** Text3D apenas. GameAssets orquestra `text3d` /
`rigging3d` / `animator3d` / `aigamekit-lab` — sem `bpy`/`trimesh` no GameAssets.

---

## Saída Hunyuan / marching cubes (típico)

- Paredes grossas / duplas, rachas minúsculas, field cheio se bbox má.
- Reparo canónico: `aigamekit_shared.mesh_repair` com perfis:

| Perfil | Uso |
|--------|-----|
| `topology_clean` | `topology-fix` / generate clean |
| `pre_decimate_uv` | Antes bake-master / simplify `_to_paint` — **não** antes do COLLAPSE no LOD texturado (trava rácio) |
| `post_decimate` | Após Decimate (LOD / remesh-textured / simplify) |
| `part_decode` | Part3D |
| `post_voxel` | Após remesh voxel |

Cadeia tip. (`topology_clean` actual): sanitize/reweld → weld → long_edges/slivers →
debris → fill (`fill_holes_sides=96`) → `make_watertight` **seletivo**
(`watertight_max_loop_diameter_ratio≈0.35`, flap-erode ON) → shade-smooth.
Opcional: morph-close / `--remove-internal-shells`. Flare/Taubin **off**;
`force_close_base` **removido** (bisect destruía cascas plástico).

Motor default: `text3d topology-fix --engine arrays` (numpy/scipy; ver
[`../TOPOLOGY_FIX_GPU_STUDY.md`](../TOPOLOGY_FIX_GPU_STUDY.md)).

**Edifícios ocos (capela):** não bisectar chão — casca plástico com fundo invertido.
**Base oca por baixo é OK** (não é bug de entregável). QA `_shape` = cortes/forma,
não “fechar chão”. Refs Text2D eye-level 3/4 via `categories.building` +
`prompt_builder`. Ver
[`../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

---

## Master DAG (GameAssets default — Round 3)

```
1 generate (shape cru, Omni) → _intermediate/id_shape.glb
2 topology-fix → id_clean (+ to_paint se preciso)
3 paint sobre clean/to_paint
4 rigging3d pipeline sobre _painted → _intermediate/id_rigged.glb
5 animator3d game-pack ×1 → id_rigged_animated.glb
6 text3d lod sobre animated/rigged → lod0/1/2 (+ finish KTX2/meshopt)
7 collision a partir do painted
8 aigamekit-lab check glb --category …
```

**`to_paint` (quando o clean precisa de decimate antes do paint):** ordem típica
`text3d simplify` → **re-**`topology-fix` (não terminar no simplify). Testes que
assertam o último `run_cmd` têm de olhar a lista completa (`call_args_list`).

Estáticos (sem rig): `text3d lod --painted-mesh` com `--target-faces` (LOD0 ≈
1.2× category target) + `--finish-lod0` (tangents/KTX2/meshopt). LOD1/2 =
target/2 e target/4 via o mesmo path textured (`remesh_textured_glb` →
`mesh_simplify`).

- Intermediários → `_intermediate/` (shape/clean/painted/rigged/…).
- Resume **tem** de procurar aí (não regenerar do zero).
- `text3d generate --no-topology-fix` mantém Stage 1 cru.
- **Abolidos no DAG:** `_rigged_hi`, `transfer-weights`×N + game-pack×N por LOD.

### Orientação / origem

- Correção Hunyuan → OpenGL (modelo de pé) + origem nos **pés** por omissão.
- Propagar por **todas** as stages. Regressão típica: `_shape` já de barriga
  para cima.
- Rigged GLB herda pivô/orientação do LOD0; esqueleto alinhado **dentro** do
  mesh — sem helpers de debug (icosphere/eixos) no export final.
- `gameassets mesh reorigin-feet` para estáticos; rigged/animados: validar
  rotação root antes de só reorigin.

### Normais / tangentes — sobreviver (não só recalcular)

Sintoma viewer: **edges vivos** ou shading “folha amassada” (normal map sem
`TANGENT`, ou V/Tri≈3). Preferir **preservar** attrs no DAG; recalcular é
fallback e falha se bpy não importar KTX2.

| Estágio | NORMAL | TANGENT | Notas |
|---------|--------|---------|-------|
| `_shape` | sim (tip. flat) | não | Hunyuan; V/Tri≈3 típico |
| `_clean` | **não** | não | `topology-fix` `export_normals=False` (watertight) |
| `_painted` | **sim** | **sim** (se UV) | Paint: `smooth_shade_scene` + `export_normals/tangents=True` |
| lod / remesh | sim | sim | shade antes de todo export com normals |
| lod final | sim | sim | finish (ver compressão) |

**Cadeia de falha medida (simple-rpg, 2026-07-23/24):**

1. Paint legado `export_normals=False` → `_painted` só `POSITION`+`TEXCOORD_0`.
2. Finish/lod bpy importa flat → export com normals → **V/Tri≈3** (edges vivos).
3. `gltf-transform prune` **sem** `--keep-attributes` apaga **TANGENT**
   (ex.: `goblin_lod0_animated`).
4. Re-`text3d finish` em GLB já KTX2: bpy sem `KHR_texture_basisu` → import
   falha → shade/tangents no-op. Fix: `ktxdecompress` → PNG antes do bpy;
   uastc re-KTX2.

**Regras de export:**

- **Não** `normals_split_custom_set(loop_normals)` → V/Tri≈3, ficheiros
  inchados (ex. goblin_shape 33 MB).
- `smooth_shade_scene` / `apply_smooth_by_angle` (60°) antes de
  `export_scene.gltf` com `export_normals=True`.
- `save_glb`: `export_tangents` **explícito** no kwargs é respeitado; senão
  auto só com nó material `NORMAL_MAP`.
- `weld_glb`: nunca engolir excepções — `log.warning`.
- Antes de **qualquer** re-export glTF (rig / animate / skin / LOD):
  `smooth_shade_scene` = **weld `1e-4` + smooth-by-angle** (modo B abaixo).

**Probe + reparar LODs já gerados:**

```bash
# attrs / V/Tri / TANGENT (sem bpy)
python3 -c "
from pathlib import Path
import sys
sys.path.insert(0,'AiGameKitLab/src')
from aigamekit_lab.glb_meta import glb_extract_meta
m=glb_extract_meta(Path(sys.argv[1]))
print(m['attributes_present'], 'v/t', m['v_per_tri'], 'T', m['has_tangents'], m['texture_mime_types'])
" public/assets/meshes/wooden_crate_lod2.glb

# reparar N+T+KTX2 sem regenerar shape/paint
text3d finish asset_lod0.glb
```

Bom: `NORMAL`+`TANGENT`, v/t ≪ 2.5, `image/ktx2` no lod final.

### V/Tri≈3 e LOD “moth-eaten” (2026-07)

Dois modos distintos — misturá-los gera fixes incompletos:

| Modo | Sintoma | O que resolve |
|------|---------|---------------|
| A — flat import | GLB sem `NORMAL` → bpy flat → exporter parte **loops** | `shade_smooth` / `smooth_shade_scene` |
| B — verts já duplicados | Mesh no ficheiro com ~3 verts por tri (SkinTokens / re-export) | **`remove_doubles` / weld** — shade sozinho **não** funde |

**Incidente (simple-rpg `shade`, 2026-07-23):**

- `_painted` / `*_lod*_painted`: V/Tri ≈ 0.66–1.1 (saudável).
- `_rigged` / `_rigged_animated`: V/Tri ≈ **3.0** (verts já partidos).
- Round 3 chama `text3d lod` **sobre animated/rigged** **sem** `--painted-mesh`
  → path `generate_lod_glb_triplet` (Decimate COLLAPSE geométrico).
- Decimate em triângulos isolados → LOD1/2 rasgados (comps ≈ nº faces;
  viewer “moth-eaten” / fios).
- Commit `eeac6b6b` (`smooth_shade` only) **não** bastava para o modo B.

**Fix no código:**

1. `smooth_shade_scene` — weld `1e-4` + smooth (Shared `bpy_mesh`).
2. `generate_lod_glb_triplet` — weld no lod0; LOD1/2 via
   `mesh_simplify.simplify_mesh_object` (mesmo path que textured /
   `text3d simplify`).
3. Path textured (`--painted-mesh`) já usava `simplify_mesh_object` desde
   `07e668e6` — por isso estáticos / painted LODs não degeneravam igual.

**Diagnóstico rápido:**

```bash
# V/Tri + counts (sem bpy)
python3 -c "
from pathlib import Path
import sys
sys.path.insert(0,'AiGameKitLab/src')
from aigamekit_lab.glb_meta import glb_extract_meta
p=Path(sys.argv[1]); m=glb_extract_meta(p)
print(p.name, 'tris=', m['triangle_count_total'], 'verts=', m['vertex_count_total'],
      'v/tri=', m['v_per_tri'])
" public/assets/meshes/shade_lod2.glb
```

- V/Tri ≈ **3.0** em rigged/animated/lod → modo B; re-gerar ladder após fix.
- V/Tri ≈ 0.5–1.5 com comps≈1 (ou poucas ilhas) → topologia partilhada OK.
- Comparar `_intermediate/{id}_painted.glb` vs `{id}_rigged_animated.glb`:
  se painted bom e rigged V/Tri=3, o split aconteceu no **rig/animate export**.

**Anti-padrão:** Decimate COLLAPSE sem weld prévio em mesh **já** V/Tri≈3
(modo B). **Não** assumir que `smooth_shade` = weld.

### Decimate / orçamento de faces (LOD texturado)

`aigamekit_shared.mesh_simplify.decimate_mesh_object` (2026-07-24):

| Armadilha | Efeito | Fix |
|-----------|--------|-----|
| ratio extremo numa passagem (0.009) | bpy COLLAPSE no-op parcial → ~22k piso | passes ≤50% (`min_ratio_per_pass=0.5`) |
| `remove_doubles` / weld **antes** do COLLAPSE em painted saudável | trava ~20k; `lod1==lod2` (ex. `wooden_crate`) | `pre_merge_threshold=0` no simplify; weld só via `smooth_shade_scene` no **export** ou em mesh já V/Tri≈3 |
| `protect_boundaries` + UV seams | protect cedo → mesmo piso | default `protect_boundaries=False` no LOD texturado |
| `pre_decimate_uv` antes do COLLAPSE no remesh | piora stall | `remesh_textured_glb`: Decimate sem pre; só `post_decimate` depois |

- Piso topológico real (ex. crate ~4.8k) pode impedir LOD2 ≪ LOD1 — textura
  deve continuar a descer (`texture_size` lod2 &lt; lod1).
- Weld **modo B** (rigged V/Tri≈3 → ladder geométrica) continua obrigatório
  no path `generate_lod_glb_triplet` (`remove_doubles` no lod0) — não confundir
  com weld pré-COLLAPSE no painted texturado.

### Compressão entregável

**Happy path (comandos + deps):** [`../GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md).

- Meshopt: bpy 5.2+ `export_meshopt_compression_enable` (+ `libmeshoptimizer-dev`
  Linux); default **ON** em `gltf_transform_finish` / bake-master / lod finish.
- KTX2/UASTC: Node + `@gltf-transform/cli` **+** CLI `ktx` (KTX-Software).
  Sem `ktx`, uastc falha (warning) — doctor verifica linha `ktx (KTX-Software)`.
  Installer Text3D: `ensure_ktx_software()` → `~/.local/opt/KTX-Software`.
- Ordem fixa: **[ktxdecompress se input já KTX2]** → shade+tangents (bpy) →
  dedup → **prune `--keep-attributes true`** → **uastc** → meshopt.
  Pós-uastc o bpy meshopt é skip (KTX2 no input) → fallback gltf-transform.
- GameAssets path rigged: `lod --no-meshopt` depois `_finish_lod_with_rollback`
  com uastc+meshopt (rollback se perder skins/clips).
- Re-comprimir / reparar N+T sem regenerar: `text3d finish asset.glb`.
- Disk: UASTC pode **crescer** JPEG pequenos; PNG grandes tipicamente encolhem
  (simple-rpg 2026-07: 162 LODs −542 MiB). Valor principal = GPU upload/VRAM.
- Sem deps: fallback gracioso; `aigamekit-lab check` pode falhar
  regras `texture_format: ktx2` / `compression: meshopt`.
- Doctor: `text3d doctor`.

### Validação

- `aigamekit-lab check glb` + `glb_meta` (parser binário, sem bpy).
- Categories: `lod0|lod1|lod2|rigged|collision` + YAML em
  `GameAssets/.../data/rules/`.
- `--no-bpy-inspect` para CI leve.

### Debug visual — `aigamekit-lab debug viz` (2026-07)

Modos: `normals` (cor), `normals-arrows`, `orientation` (backface vermelho),
`uv` (checker), `edges` (boundary/non-manifold), `weights`
(dominant/count/unweighted/`--bone`); `--wireframe` transversal.
PNGs com legenda Pillow + `viz_report.json`. Fonte: `AiGameKitLab/src/aigamekit_lab/viz.py`.

Armadilhas descobertas (valem para qualquer análise bmesh de GLBs da pipeline):

- **Seam-splits do glTF = falsos positivos.** Export glTF splita vértices por
  UV/normal; pós-import **toda** edge de seam parece boundary e as ilhas
  fragmentadas inflam a estimativa de faces invertidas
  (`recalc_face_normals` diff). Weld curto (`remove_doubles` 1e-4) antes das
  métricas — medido no bandit_lod0: boundary 15 824 → **0** (watertight real,
  5 non-manifold), flipped 30 % → **0 %**. Mesma técnica do `cut_review`.
- **Modifier WIREFRAME explode em geometria suja.** `use_even_offset` (default)
  escala 1/sin(θ) — slivers/degenerados atiram vértices para ±32k e a câmara
  auto-frame vai atrás (render "vazio"). Desligar `use_even_offset` +
  `use_relative_offset` em overlays de debug.
- **Rigged renderiza em pose; `obj.data.vertices` é rest.** Geometria derivada
  (setas de normais, overlays) tem de amostrar o mesh avaliado
  (`evaluated_depsgraph_get()` → `obj.evaluated_get(deps).to_mesh()`), e
  duplicados de overlay têm de **manter** o modifier ARMATURE.
- **KTX2/meshopt**: todo import de deliverable passa por
  `aigamekit_shared.gltf_decode.bpy_readable_glb` (decode automático via
  `@gltf-transform/cli`). `import_gltf` default = `bone_heuristic=TEMPERANCE`
  — evita materializar meshes de display dos bones (`Icosphere`). O default
  do `bpy.ops.import_scene.gltf` é `BLENDER`, que **cria** esses helpers e
  infla world bounds no re-export; não usar `BLENDER` no happy path.
- **`strip_bone_display_meshes` só com armature na cena** — a heurística
  "tiny orphan mesh ≤64 verts sem material" apagava assets estáticos
  legítimos (cubo de teste inteiro).
- Legendas Pillow: `ImageFont.load_default()` é ASCII-only — acentos/setas
  viram mojibake.

### LOD0 terminal

| Pipeline chegou a… | LOD0 deve ser |
|--------------------|---------------|
| animate | GLB animado |
| rig sem animate | rigged |
| só paint | painted (+ lod finish) |

### Promote (Stage 9.5) + resume — armadilhas

Após animate, winners `*_lodN_animated.glb` viram `meshes/{id}_lodN.glb`.
Alias runtime: `publish_rigged_animated_alias` → `{id}_rigged_animated.glb` ← lod0.

**Bug (simple-rpg ogre/witch, 2026-07):** resume re-corria bake-master/LOD e
via `faces(lod0) < 99% painted` fazia `copy2(painted → lod0)` — apagava
skins/clips. Animated sobrevivia só em `_intermediate/*_lodN_painted.glb`
(nome enganador do archive do promote).

**Fix (`pipeline.py`):**

1. bake-master / LOD **skip** se lod0 já é entregável promovido
   (`_glb_is_promoted_animated` / `_glb_is_promoted_rigged`).
2. Archive pré-promote → `*_lodN_pre_promote.glb` (não colidir com
   `*_lodN_painted` do bake).
3. `gltf_transform_finish` pós-promote: se perder skins/clips → rollback
   para cópia pré-finish.

**Diagnóstico rápido:**

```bash
# lod0 público sem anim vs archive bom
python3 -c "import struct,json,sys; p=sys.argv[1]; d=open(p,'rb').read(); o=12
while o<len(d):
 n,t=struct.unpack_from('<I4s',d,o); o+=8; c=d[o:o+n]; o+=n
 if t==b'JSON': j=json.loads(c); print(p,'anims',len(j.get('animations')or[]),'skins',len(j.get('skins')or[])); break
" public/assets/meshes/boss_ogre_lod0.glb
```

Se `anims=0` mas `_intermediate/{id}_lod0_painted.glb` (legado) ou
`*_lod0_pre_promote.glb` tem clips → republicar archive → `*_lod0.glb` e
corrigir pipeline (já no código). Manifest/`game.yaml` com `rig`+`animate`
**não** bastam se resume clobberar.

---

## Rig / animate (modelos adjacentes)

- Rigging3D: SkinTokens, Python 3.13 + bpy≥5.2.
- Animator3D: `game-pack` humanoid → retarget Quaternius (não os clips procedurais
  do preset README, salvo fallback). Ver
  [`ANIMATOR_RETARGET_FINDINGS.md`](ANIMATOR_RETARGET_FINDINGS.md).
- **Bípedes “creature” no manifesto** (shade, bandit, goblin…): usar
  `animate.preset: humanoid` + `force_preset: true` + clips
  `idle,walk,run,jump,attack,hit,death`. `preset: creature` + `procedural: true`
  gera ossos estranhos + clips `Animator3D_*` que **não** batem com scripts
  VibeGame (`idle`/`walk`/…). Detalhe: findings de animate.
- **Pivô animado:** `root` estático nos pés; **não** retargetar rotação do root
  Quaternius (±90° → origem salta para a cintura ao play). Location só em `pelvis`.
- Transfer weights: `rigging3d transfer-weights` (não Text3D; fora do DAG Round 3).
- `text3d lod` preserva armatures/animations.

Pose Omni: T-pose Quaternius para humanoids; A-pose para corpos gordos/músculo
(estica menos). Quad/serpente: T-pose humana **não** ancora bem — ver Omni
findings (semântica).

---

## Árvores derrubáveis — split-at-height (antes do LOD)

DAG em `_run_split_lod_stages` (`pipeline.py`) quando `wants_split_at_height`:

```
painted → text3d split-at-height --no-cap --split-files
       → stump_painted + top_painted
       → LOD stump 0/1/2 + LOD top 0/1/2
       → compose lodN = Stump + Top (nomes para DestructiblePlugin fall)
       → stump_collision + top_collision (+ collision = cópia do stump)
```

### Quem activa o split

`wants_split_at_height(profile, row)`:

| `text3d.split_at_height` | Comportamento |
|--------------------------|---------------|
| `false` | Off |
| `true` ou omitido | Só assets **tree-like** |

Tree-like = `category ∈ {tree, vegetation}` **e** (`bbox_preset == tree` **ou**
id/idea match `tree|pine|oak|willow|cactus|fir|spruce|palm`). Rocks/props
**nunca** partem, mesmo com flag global `true`.

### Cut-only (default 2026-07)

- Default: **só bisect** (`cap=False`, `use_fill=False`) — buraco no plano de
  corte fica aberto. Fechos (fill/raster/fuse/UV bark) geravam artefactos
  (tampões flutuantes, UV léak); código legado atrás de `--cap` / `cap=True`.
- Fingerprint `SEAL_VERSION=cut-only-v1` em `aigamekit_shared.mesh_split` —
  resume invalida stump/top/lod/collision se o seal antigo diferir.
- Altura default do corte: `min(0.8 m, altura/4)` acima da base.

### Resume / invalidar

```bash
# Apaga derivados do split; mantém *_painted.glb
# (helper: invalidate_split_artifacts(mesh_final))
gameassets resume … --redo-split   # ou AIGAMEKIT_REDO_SPLIT=1
```

Apaga: `*_stump_painted` / `*_top_painted`, dirs `*_stump_lod`/`*_top_lod`,
lods compostos, `*_stump_collision` / `*_top_collision` / `*_collision`.

### Runtime (VibeGame)

- Visual: `*_lod0.glb` com meshes `Stump` + `Top`.
- Collider fall: `*_stump_collision.glb` (não o hull da árvore inteira).
- Review rápido sem regenerar LOD:
  `aigamekit-lab debug cut-review stump.glb -o /tmp/cut/ [--cut-height 0.8]`

---

## LOD texturado — Decimate COLLAPSE não conhece ilhas UV (2026-08)

**Sintoma:** `mushroom_red_painted.glb` perfeito (75k faces), `mushroom_red_lod0.glb`
(1440 faces) com as pintas brancas rasgadas em estilhaços; no `lod2` (480 faces)
vira salada de textura. Não era o orçamento de faces (`lod_budget` correcto) nem
o `split-at-height`: `mushroom_glow` (sem split, mesmo orçamento) tinha o mesmo
defeito.

**Causa:** o Decimate COLLAPSE do bpy optimiza só erro **geométrico**. Os
vértices das costuras UV colapsam através das ilhas do atlas e as UVs esticam.
Delator barato: **V/Tri sobe** com a decimação (1.17 → 1.55 na escada antiga) —
o exporter parte cada vez mais loops porque as costuras se multiplicam.

**Correcção — duas rotas em `remesh_textured_glb`:**

| Rota | Quando | Como | Atlas |
|------|--------|------|-------|
| Atlas preservado | alvo ≥ piso de costuras | `gltf-transform simplify` (meshoptimizer) | intacto |
| Atlas refeito | alvo < piso de costuras | decimate + `xatlas` + closest-point rebake | repintado |

O **meshoptimizer** trata costuras de atributos como fronteiras bloqueadas — desce
faces sem tocar nas UVs. Em troca tem um **piso de costuras**: no cogumelo parou
em 2402 faces com qualquer rácio. Abaixo disso a única saída correcta é descartar
as UVs e repintar (`_rebake_textured_lod`).

**Segundo gatilho do rebake: V/Tri.** Um atlas intacto mas cheio de costura sai
caro em vértices. `MESHOPT_MAX_V_PER_TRI = 1.35`, medido **no intermédio**, antes
do re-export — o `smooth_shade_scene` a 60° torna creases em arestas duras e o
exporter parte loops: malhas sãs inflacionam 2–3%, as cheias de costura 11–15%
(spear 1.451 → 1.662; swamp_shack 1.533 → 1.702). 1.35 × 1.15 = 1.55, dentro do
tecto 1.6 das regras LOD. Sem esta guarda o `swamp_shack_lod0` saía com V/Tri
1.70 **e** 8.7% acima do orçamento de faces; pelo rebake acerta 28566/28567 com
V/Tri 1.37.

**Resíduo conhecido:** em formas finas/recortadas (spear, swamp_shack) o xatlas
cria muitos charts pequenos e o V/Tri dos `lod1`/`lod2` fica ~1.6–1.8, acima da
regra. Continua melhor que a rota antiga (2.03 / 2.15) e as UVs estão íntegras —
é custo de charts + arestas duras, não rasgadura.

**O rebake não passa por voxel remesh.** A mesh decimada é um subconjunto da
original, logo o closest-point cai na superfície certa. Com voxel remesh (versão
antiga, só accionada em stall) a casca reconstruída caía do lado errado de paredes
duplas Hunyuan → "salada de textura" da chapel.

**Não fazer:**
- COLLAPSE com o atlas original em rácios agressivos (é o bug acima).
- `gltf-transform weld` com tolerância ou `--lock-border` antes do simplify: funde
  as costuras e o meshoptimizer volta a colapsar através das ilhas.
- Reconstruir o material do zero sem copiar `Roughness`/`Metallic` do painted — o
  BSDF default tem specular alto e lava a cor (vermelho → salmão).

**Fidelidade ao painted, não ao LOD anterior.** No `crystal_blue` o painted tem
contornos claros nas facetas; o `lod0` antigo tinha-os esmagado em azul liso
(UVs esticadas) e o novo repõe-nos. Ao comparar LODs, a referência é o
`_painted.glb` — um LOD "mais suave" pode ser simplesmente detalhe perdido.

**Resultado medido (`mushroom_red`, mesmo orçamento de faces):**

| | faces | verts | V/Tri | bytes |
|---|---|---|---|---|
| lod0 antigo | 1439 | 1681 | 1.17 | 876 KB |
| lod0 novo | 1438 | 1019 | 0.71 | 575 KB |
| lod2 antigo | 478 | 740 | 1.55 | 94 KB |
| lod2 novo | 480 | 395 | 0.82 | 67 KB |

Código: `Text3D/src/text3d/utils/gltf_finish.py` (`meshopt_simplify_glb`),
`Text3D/src/text3d/utils/mesh_remesh_textured.py` (`_meshopt_preserve_atlas`,
`_rebake_textured_lod`). Testes: `Text3D/tests/test_lod_textured_routes.py`.

---

## Rig por nível de LOD — o esqueleto custa no runtime (2026-08-16)

`text3d lod` preservava armature/skin em **todos** os níveis: cada prop rigado
entregava lod0, lod1 e lod2, cada um com o seu esqueleto. Medido no browser
(`simple-rpg`, profiler + `scene.traverse`): **11 470 ossos** e **12 308 de
15 086 nós da cena escondidos** — a esmagadora maioria eram níveis de LOD que
ninguém vê animar. `Object3D.updateMatrixWorld` ignora `visible` e recompõe tudo
por frame, por isso esses níveis custavam mais que a geometria desenhada
(`updateMatrixWorld` ~2,1 ms de um frame de 21 ms).

**Política:** `--rig-max-level` (default **1**) — lod0/lod1 animam, lod2 sai
mesh estático. `0` = só o lod0 anima (props); `2` = comportamento antigo.

- Caminho **geométrico**: `_finalize_geometric_lod(keep_rig=False)` →
  `strip_rig_in_scene` (congela a pose, remove modifier Armature, limpa vertex
  groups, apaga a armature, repõe `matrix_world`) e exporta sem
  `export_skins`/`export_animations`.
- Caminho **texturizado**: `lod_levels_with_rig()` decide quais níveis levam
  `transfer_skin_to_mesh`; os restantes ficam sem rebind.
- **GameAssets** (`RIG_MAX_LEVEL`, tem de bater certo com `DEFAULT_RIG_MAX_LEVEL`):
  passa `--rig-max-level` e — importante — o gate `ladder_ok` e o rollback do
  finish passaram a ser **por nível**. Sem isso, exigir `skins[]` no lod2 punha a
  ladder a regenerar-se todos os runs e o finish em rollback permanente.
- As regras `lod1.yaml`/`lod2.yaml` não pedem `JOINTS_0`/`WEIGHTS_0`, logo a
  validação não muda.

Do lado do motor o complemento é destacar do grafo os níveis inactivos
(`VibeGame/src/extras/gltf-lod-parking.ts`): cena 15 086 → 5 674 nós, grupo
`render` 7,26 → 4,93 ms.

---

## LOD geométrico / rigado — mesmo bug, sem rebake (2026-08)

Round 3: `text3d lod` sobre `_rigged_animated` **sem** `--painted-mesh`. Antes =
Decimate COLLAPSE bpy (cego a UV). Sintoma: `boss_ogre` lod1≡lod2 @5988 faces,
V/Tri 2.23, textura em salada; `wolf` lod2 V/Tri a subir 1.19→1.65.

**meshopt simplify em skinned funciona.** Mantém `JOINTS_0`/`WEIGHTS_0`/
`TEXCOORD_0`, skins e clips. Y dos pés quase intacto. O que partia origem era
só a **compressão** meshopt+quantize do finish — não o simplify. `weld=False`
obrigatório (weld funde verts com weights diferentes).

**Diferença face ao path texturado:** no piso de costuras **não** há rebake
(xatlas mata o layout de weights; `transfer_weights` saiu do DAG). Política:
**aceitar faces acima do orçamento**. Medido:

| | alvo lod2 | COLLAPSE (antigo) | meshopt piso |
|---|---|---|---|
| goblin | 4135 | 4135 | **4134** (atinge) |
| wolf | 7941 | 7941 (rasga) | **~13276** |
| boss_ogre | ~6000 | 5988 stall+salada | **~28980** |

SSIM vs painted (ogre ¾): lod2 COLLAPSE 0.21 → meshopt@29k 0.30.

Código: `generate_lod_glb_triplet` → `_meshopt_simplify_level` /
`_generate_lod_glb_triplet_bpy_collapse` (fallback). Testes:
`Text3D/tests/test_lod_geometric_meshopt.py`.

---

## Checklist pós-shape (antes paint)

QA no **`_shape` fresco** (não GLBs antigos de `public/`):

1. 3 views (`aigamekit-lab debug screenshot` — `front,three_quarter,right`) —
   cortes graves, planos a régua (clip MC/bbox), forma derretida/errada.
2. Bounds / MB razoáveis (não 300 MB achatado / field cheio).
3. Semântica OK vs `idea` (lobo≠raposa bípede).
4. Sidecar `.omni.json` coerente com manifest (`size_m`, bbox_preset).
5. Origem/orientação visual OK → então topology-fix → paint.

**Ignorar:** base oca / vista para dentro da casca por baixo. `force_close_base`
removido de propósito.

**Pós-LOD (manual ou batch):**

1. Face count de `*_lod0` ≪ clean/painted high-poly. Incidente watchtower:
   `lod0` sobrescrito com ~333k (= clean) — restaurar via `text3d lod`.
2. **V/Tri** em `*_lod1` / `*_lod2` (e rigged/animated): se ≈ **3.0** → modo B
   (verts duplicados); LOD rasgado até re-gerar com weld/`mesh_simplify`.
   Ver secção [V/Tri≈3](#vtri3-e-lod-moth-eaten-2026-07).

---

## Changelog

| Data | Nota |
|------|------|
| 2026-08-16 | Rig por nível: `--rig-max-level` (default 1) — lod2 estático; gate/rollback por nível no GameAssets; motor destaca LODs inactivos do grafo |
| 2026-08-06 | LOD texturado: meshoptimizer (atlas preservado) + rebake xatlas sem voxel (decimação extrema); COLLAPSE com atlas original rasgava UVs |
| 2026-08-06 | LOD geométrico/rigado: meshopt-first (`weld=False` skinned); piso de costuras aceite (sem COLLAPSE abaixo; sem rebake no DAG) |
| 2026-08-06 | Finish KTX2 híbrido: ETC1S albedo/MR/AO/emissive + UASTC `*normal*`; intermédios bpy exportam JPEG (não PNG) |
| 2026-07-24 | Doc fix: `import_gltf` default=`TEMPERANCE` (não BLENDER); BLENDER materializa Icosphere helpers |
| 2026-07-24 | `debug viz` (6 modos); weld antes de métricas boundary/flipped (seam-splits); WIREFRAME sem even_offset; depsgraph p/ rigged; `gltf_decode` (KTX2/meshopt); strip helpers só com armature |
| 2026-07-24 | N/T sobreviver: paint exporta N+T; finish `ktxdecompress`+prune `--keep-attributes`; Decimate stepwise; weld pré-COLLAPSE só modo B (não painted texturado) |
| 2026-07-24 | V/Tri≈3 modo B: weld obrigatório; LOD geométrico Round 3 moth-eaten; `smooth_shade_scene`+`mesh_simplify` |
| 2026-07-24 | Estáticos: LOD0=1.2×target + `--finish-lod0` (não identity painted) |
| 2026-07-24 | Compressão: defaults meshopt ON; `ktx` obrigatório p/ UASTC; `text3d finish`; doc [`GLB_FINISH_COMPRESSION.md`](../GLB_FINISH_COMPRESSION.md); batch simple-rpg −542 MiB |
| 2026-07-24 | `to_paint`: simplify → re-`topology-fix` (não assert só último cmd) |
| 2026-07-24 | Split-at-height: DAG stump/top LOD+collision; tree-like only; cut-only default; `--redo-split` |
| 2026-07-24 | Link `ANIMATOR_RETARGET_FINDINGS` — root estático / cintura-ao-play |
| 2026-07-24 | DAG Round 3; QA `_shape` (cortes/forma); base oca ignorável |
| 2026-07-24 | Promote/resume: não clobber lod0 promovido; archive `_pre_promote`; finish rollback |
| 2026-07-24 | fill=96 + diameter guard; engine `arrays`; link TOPOLOGY_FIX_GPU_STUDY |
| 2026-07-24 | `topology_clean` sem force_close_base/flare/Taubin; prompts building; watertight seletivo |
| 2026-07-24 | Ops: após `text3d lod` manual, verificar face count — nunca deixar `*_lod0` = clean high-poly |
| 2026-07-19 | Compilado de AGENTS.md + ops master pipeline / Omni batch |
