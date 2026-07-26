# Lições: Hunyuan3D shape, repair e Part3D

Notas operacionais (experiências simple-rpg / watchtower / chapel, ~2026-07).
Complementa `Text3D/AGENTS.md`, `Part3D/README_PT.md` e o perfil Shared `topology_clean`.
Findings mesh: [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md).
Paint: [`findings/PAINT_PART_FINDINGS.md`](findings/PAINT_PART_FINDINGS.md).
EN (resumo): [`HUNYUAN_MESH_AND_PARTS_LESSONS.md`](HUNYUAN_MESH_AND_PARTS_LESSONS.md).

## 1. Hunyuan3D shape (Text3D)

### O que o modelo faz bem / mal

| Sintoma | Causa típica | O que repair / Part3D consegue |
|---------|--------------|--------------------------------|
| “Pés de elefante” na base | Marching cubes engorda contacto com o chão; prompt pode pedir plinto largo | Mitigar na **geração** (prompt). `clamp_base_flare` está **off** no `topology_clean` actual |
| Escada / bandeira / varão finos “soldados” | Geração a partir de uma vista + MC; seed e prompt dominam | Repair **não** separa semanticamente; Part3D `faces` só corta faces com label |
| Casca “plástico” / double shell (edifícios) | Exterior + interior; fundo vira para dentro (capela) | **Não** bisectar chão. Preferir **prompt/vista** + morph-close fino; strip de cascas só com cuidado (§2) |
| Portas/janelas tapadas após clean | Fill / caps sem limite de diâmetro | Watertight seletivo: `watertight_max_loop_diameter_ratio` |
| Sino / prop no oco apagado | Strip de cascas com hit à escala do oco (`room_gap`) | Critério deprecated; perfil default **off**; CLI auto-ON só hollow/building |
| Silhueta “argila” | Ruído MC | Taubin **off** no perfil actual |

### Seed e prompt (edifícios)

- **Seed muda o asset.** Não comparar Part3D entre seeds sem regenerar shape.
- **Ângulo Text2D manda no i2m.** Worm’s-eye / underside → fundo aberto e casca oca.
- Categoria `building` (`GameAssets/src/gameassets/categories.py`):
  - `hint_2d`: eye-level three-quarter, câmera ligeiramente acima, fundação fechada, paredes opacas
  - `hint_3d`: base fechada, sem hollow shell / terrain slab
  - `extra_negatives`: worm’s-eye, underside, cutaway, floating, …
- `prompt_builder.enhance_prompt_for_pipeline`: ref i2m aplica **`hint_2d` + `hint_3d`** (antes só `hint_3d` → ângulo nunca entrava).
- Em GPUs ~6 GB, `--quality high` pode ser **capado pelo hw-auto**. Ver banner `Hardware (auto)`.

### Artefactos de referência

- Imagens: `VibeGame/examples/simple-rpg/public/assets/images/<id>.png`.
- Capela: regenerar PNG com framing 3/4 ligeiramente de cima **antes** de shape→clean→paint.
- Paint: mesma imagem do i2m; GameAssets usa `ensure_clean_for_paint` (pinta `_clean`, não `_shape`).

## 2. Repair (`topology_clean` / `text3d topology-fix`)

Perfil: `Shared/src/gamedev_shared/mesh_repair.py`. Entrada: `prepare_mesh_topology`.
Motor: `--engine arrays` (default) — ver [`TOPOLOGY_FIX_GPU_STUDY.md`](TOPOLOGY_FIX_GPU_STUDY.md).

### Perfil actual (lean + watertight seletivo)

| Knob | Valor | Nota |
|------|-------|------|
| `fill_holes_sides` | **96** | Micro-rachas MC; portas grandes sobrevivem via diâmetro |
| `watertight` | **True** | Caps + fill; `watertight_max_loop_diameter_ratio≈0.35` |
| `watertight_cap_base` | True | Caps planares de base — **não** é bisect `force_close_base` |
| `do_remove_internal_shells` | **False** | CLI `--remove-internal-shells` / auto ON building |
| `do_clamp_base_flare` / `do_taubin` | **False** | Desligados |
| `force_close_base` | **removido** | Bisect+chão destruía cascas plástico |

### Ordem

reweld → weld → dissolve/loose → long edges → slivers → debris →
fill → watertight seletivo → (opcional) strip cascas / morph-close →
normais → shade-smooth.

### CLI

```bash
text3d topology-fix id_shape.glb -o id_clean.glb --export-origin feet
# --no-watertight | --fill-holes-sides N
# --morph-close <m> | --morph-close-voxels N   # funde double-shell fino
# --remove-internal-shells | --keep-internal-shells
# --category building | --bbox-preset …
# --engine arrays|bpy
```

### Armadilhas (capela / edifícios)

1. **Não reintroduzir `force_close_base`.** Fundo Hunyuan ≠ buraco a tapar — a casca vira para dentro; bisect cria geometria falsa / saia. **Base oca por baixo é OK** em jogo (câmara não olha para baixo) — QA do `_shape` foca cortes/forma, não “fechar chão”.
2. **Strip room-scale** apaga sino e props no oco. Só sanduíche fino (wall_gap) é seguro; default off (`--remove-internal-shells` auto ON building = sanduíche fino).
3. **Paint não cura double-shell.** Inpaint em ilhas nunca baked → interiores deformados. Usar clean correcto + `paint_prep.restrict_inpaint`.
4. Morph-close alto derrete escadas/bandeiras. Auto = Nxvoxel pequeno.

### Histórico (porquê removemos o chão forçado)

Documentação antiga descrevia `force_close_base` + flare + Taubin como defaults.
Na prática (capela): casca tipo plástico com fundo invertido; forçar laje e strip
agressivo destruíam sino/portas. Mitigação canónica passou a **prompt/vista** +
watertight seletivo + morph-close opcional.

## 3. Part3D (P3-SAM + X-Part + faces)

### Modos de export (`--parts-mode`)

| Modo | Comportamento | Quando usar |
|------|---------------|-------------|
| **`faces`** (default) | Face-split na topologia original | Aparência / props com finos (escada, bandeira, trilhos) |
| `xpart` | Regen volumétrica por parte | Sólidos grossos; **derrete** finos e piora pés |
| `hybrid` | X-Part + face fallback | Só se precisares de sólidos e aceitares risco |

**Descoberta principal:** para watchtower / edifícios com apêndices finos, o entregável visual bom é **face-split**, não X-Part. `watchtower_parts_best.glb` ≈ faces com ~10 meshes (escada e bandeira legíveis). X-Part / hybrids agressivos → doubles, melt, quarantine sob `_broken_hybrid/`.

Defaults: `DEFAULT_PARTS_MODE = "faces"`, exclusive partition OFF, `preserve_thin_topology` OFF (colar faces+carve em feature soldada → escada dupla / buraco na parede).

### Segmentação: `p3sam` vs `--fine-parts` / `hybrid`

| Preset | Segment mode | Efeito observado (watchtower seed “soldado”) |
|--------|--------------|-----------------------------------------------|
| Default | `p3sam` | Quase separa a **escada** inteira; **bandeira** fica no corpo |
| `--fine-parts` | `hybrid` (P3-SAM + snap geométrico); **detail-levels=0** | Separar **bandeira**; escada parte-se e deixa pedaço soldado |

`--fine-parts` **não** liga `--detail-levels` (força 0). Detail hierárquico (`--detail-levels 1|2`) é outro eixo: re-segmenta labels grandes; pode fragmentar peels já bons se correr em labels finas.

### Fusão de peels (escada + bandeira)

Quando um run é bom na escada e outro na bandeira (mesma topologia de faces):

- Utilitário: `part3d.utils.label_fuse.fuse_protrusion_labels`
- Ideia: manter labels “thin” do **base** (p3sam); acrescentar peels thin do **donor** (hybrid) que cobrem faces novas
- Requer `face_ids` do **mesmo** mesh (mesmo N faces). Proxy 100k ≠ lod0 24k — usar `label_transfer` ou segmentar os dois no mesmo GLB.

Detail: `large_region_candidates` **salta** labels já thin (aspect alto) para não re-partir escada/bandeira.

### Proxy high-poly

- >~200k faces: remesh 50k–120k (`text3d remesh`) + `--segmentation-proxy`, depois transfer de labels.
- LOD decimado só se continuar conectado; LOD rachado piora P3-SAM.

### VRAM (~6 GB)

- UMS pode recusar `part3d` (peak estimado alto) → fallback in-process.
- Concurrent paint3d / blender snap / outro job → OOM a meio do P3-SAM.
- Antes de decompose: `ums status` + NVML (`query_gpu_free_mib` / `list_gpu_snapshots`); preferir GPU livre; `--point-num` / `--prompt-num` / `--quality fast` se preciso.
- **Não sobrescrever** `*_lod0.glb` com clean high-poly por engano — confirmar face count após LOD (`~` target-faces).

## 4. Critério de qualidade (o que julgar)

Para props arquitetónicos com finos:

1. **Aparência** (silhueta, escada contínua, bandeira ligada ao mastro) > sólidos X-Part watertight.
2. Face-parts com N meshes legíveis (corpo + peels) > 2–3 blobs grossos.
3. Comparar sempre **shape/clean** do mesmo seed antes de culpar Part3D.
4. Edifícios: base fechada / sem worm’s-eye na ref; portas abertas após clean.
5. Screenshots: `gamedev-lab debug screenshot` com nomes de vista (`front,three_quarter`), não `--views 4`.

## 5. Fluxo recomendado (props tipo watchtower / chapel)

```bash
# Building: regenerar PNG com framing eye-level 3/4 se a base vier oca
text2d generate "$PROMPT_COM_HINTS_BUILDING" -o public/assets/images/id.png --quality high

text3d generate --from-image public/assets/images/id.png --quality medium \
  --no-topology-fix -o _intermediate/id_shape.glb --export-origin feet

text3d topology-fix _intermediate/id_shape.glb \
  -o _intermediate/id_clean.glb --export-origin feet --category building

paint3d texture _intermediate/id_clean.glb \
  -i public/assets/images/id.png -o _intermediate/id_painted.glb --quality medium

# LOD / parts conforme pipeline master (GameAssets batch)
```

## 6. Código de referência

| Peça | Path |
|------|------|
| Perfil repair | `Shared/src/gamedev_shared/mesh_repair.py` (`topology_clean`, `make_watertight`, `remove_internal_shell_faces`) |
| Arrays engine | `Shared/src/gamedev_shared/mesh_repair_arrays.py` |
| Topology Text3D | `Text3D/src/text3d/utils/mesh_lod.py` (`prepare_mesh_topology`) |
| Hints building / i2m | `GameAssets/src/gameassets/categories.py`, `prompt_builder.py` |
| Clean antes paint | `GameAssets/src/gameassets/pipeline.py` (`ensure_clean_for_paint`) |
| Inpaint restrito | `Paint3D/src/paint3d/paint_prep.py` |
| Defaults Part3D | `Part3D/src/part3d/defaults.py` |
| Face-split / fuse | `Part3D/.../face_split.py`, `label_fuse.py`, `label_transfer.py` |
| Experimento local | `VibeGame/examples/simple-rpg/_part3d_watchtower/` (gitignored) |

## 7. O que não fazer

- Defaultar X-Part para assets com escadas/bandeiras/antenas.
- Reintroduzir `force_close_base` / flare / Taubin como default do clean.
- Strip agressivo de cascas (room-scale) em edifícios com props no oco.
- Gerar building com worm’s-eye e esperar base fechada no mesh.
- Pintar `_shape` quando deve existir `_clean`.
- Julgar Part3D em seed B com expectativas do seed A.
- Correr paint + part3d na mesma 6 GB sem UMS estável.
