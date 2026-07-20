# Lições: Hunyuan3D shape, repair e Part3D

Notas operacionais (experiências simple-rpg / watchtower / chapel, ~2026-07).
Complementa `Text3D/AGENTS.md`, `Part3D/README_PT.md` e o perfil Shared `topology_clean`.

## 1. Hunyuan3D shape (Text3D)

### O que o modelo faz bem / mal

| Sintoma | Causa típica | O que repair / Part3D consegue |
|---------|--------------|--------------------------------|
| “Pés de elefante” na base | Marching cubes engorda contacto com o chão; por vezes o prompt também pede plinto largo | `clamp_base_flare` mitiga flare **radial** vs mid-height; não apaga um plinto arquitetónico intencional |
| Escada / bandeira / varão finos “soldados” ao volume | Geração a partir de uma vista + MC; seed e prompt dominam | Repair **não** separa semanticamente; Part3D faces só **corta faces** onde há label |
| Base oca / shell sem chão | Shell manifold com boundary=0 mas sem laje inferior | `force_close_base` (só se recess_ratio alto no eixo certo) |
| Silhueta “argila” | Ruído MC | Taubin (volume-preserving) no `topology_clean` |

### Seed e prompt

- **Seed muda o asset.** Uma watchtower “boa” (escada/bandeira quase livres) e outra do mesmo prompt com seed diferente podem ter finos bem mais soldados. Comparar Part3D entre seeds sem regenerar shape é enganador.
- Prompt ajuda (base flush, “no flared footings”, “thin ladder with separate rungs”, “flagpole continuous, no gaps”) mas **não garante** separação geométrica.
- Em GPUs ~6 GB, `--quality high` no CLI pode ser **capado pelo hw-auto** (steps/octree mais baixos). Ver banner `Hardware (auto)` no log.

### Artefactos de referência

- Imagens Text2D do batch: `VibeGame/examples/simple-rpg/public/assets/images/<id>.png` (ex. `watchtower.png`).
- Para paint alinhado ao shape novo, regenerar Text2D com o mesmo prompt/`--save-reference-image` no `text3d generate` se a silhueta mudou muito.

## 2. Repair (`topology_clean` / `text3d topology-fix`)

Perfil em `Shared/src/gamedev_shared/mesh_repair.py`. Entrada Text3D: `prepare_mesh_topology`.

### Defaults que importam

| Knobs | Valor útil | Porquê |
|-------|------------|--------|
| `fill_holes_sides` | **32** (perfil) | 64 fundia aberturas finas (bandeira / frestas) |
| `watertight_skip_flap_erode` | **True** | Erode de abas “come” escadas/bandeiras coladas à parede |
| `clamp_base_flare` | on, ratio ~1.03–1.06, bottom ~10–15% | Falloff smoothstep evita lip no corte superior |
| `do_taubin` | 3 iters | Suaviza MC sem remesh isotrópico agressivo |
| `force_close_base` | on, recess_trigger ~0.25 | Só shells sem chão |

### Armadilhas

1. **Não forçar `fill_holes_sides ≥ 64` no watertight.** Código antigo em `mesh_lod` fazia `max(sides, 64)` e anulava o perfil — removido; `None` no CLI = perfil.
2. **Eixo up no Blender pós-glTF.** Mundo Blender é Z-up; `v.co` local glTF costuma ser Y-up. `force_close_base` / `base_openness_stats` devem usar `infer_up_axis` (AABB mais longo). Up fixo = Y no mundo → **laje lateral / saia** em torres sólidas.
3. **Métrica de flare.** Medir raio a partir do **centro mid-height**, não da origem mundo (torre pode estar offset).
4. **Clamp ≠ “base quadrada perfeita”.** Se o Hunyuan gerou base larga como pedra de fundação, o clamp só puxa o overshoot vs corpo.

### Ordem útil no perfil

reweld → weld → slivers/debris → fill (≤32) → watertight (skip flap-erode) → `force_close_base` → `clamp_base_flare` → Taubin → shade-smooth (Text3D).

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
- Antes de decompose: `nvidia-smi`; preferir GPU livre; `--point-num` / `--prompt-num` / `--quality fast` se preciso.
- **Não sobrescrever** `*_lod0.glb` com clean high-poly por engano — confirmar face count após LOD (`~` target-faces).

## 4. Critério de qualidade (o que julgar)

Para props arquitetónicos com finos:

1. **Aparência** (silhueta, escada contínua, bandeira ligada ao mastro) > sólidos X-Part watertight.
2. Face-parts com N meshes legíveis (corpo + peels) > 2–3 blobs grossos.
3. Comparar sempre **shape/clean** do mesmo seed antes de culpar Part3D.
4. Screenshots: `gamedev-lab debug screenshot` com nomes de vista (`front,three_quarter`), não `--views 4`.

## 5. Fluxo recomendado (props tipo watchtower)

```bash
# Shape (guardar PNG se fores pintar já)
text3d generate "$PROMPT" --quality high --no-topology-fix \
  -o _intermediate/id_shape.glb --save-reference-image

text3d topology-fix _intermediate/id_shape.glb \
  -o _intermediate/id_clean.glb --export-origin feet

paint3d texture _intermediate/id_clean.glb \
  -i public/assets/images/id.png -o _intermediate/id_painted.glb --quality high

text3d lod _intermediate/id_shape.glb \
  --painted-mesh _intermediate/id_painted.glb \
  --target-faces 24000 -o meshes/ -n id --no-finish

# Partes: faces na malha de jogo (ou proxy+transfer se clean >> 200k)
part3d decompose meshes/id_lod0.glb -o _part3d/id_parts.glb \
  --output-segmented _part3d/id_segmented.glb \
  --parts-mode faces --quality medium --allow-shared-gpu

# Se escada OK / bandeira falha (ou o contrário): 2º run hybrid no MESMO GLB + fuse_protrusion_labels
```

## 6. Código de referência

| Peça | Path |
|------|------|
| Perfil repair | `Shared/src/gamedev_shared/mesh_repair.py` (`topology_clean`, `clamp_base_flare`, `force_close_base`, `infer_up_axis`) |
| Topology Text3D | `Text3D/src/text3d/utils/mesh_lod.py` (`prepare_mesh_topology`) |
| Defaults Part3D | `Part3D/src/part3d/defaults.py` |
| Face-split | `Part3D/src/part3d/utils/face_split.py` |
| Detail hierárquico | `Part3D/src/part3d/utils/hierarchical.py` |
| Fuse peels | `Part3D/src/part3d/utils/label_fuse.py` |
| Transfer proxy→hi | `Part3D/src/part3d/utils/label_transfer.py` |
| Experimento local | `VibeGame/examples/simple-rpg/_part3d_watchtower/` (gitignored `**/_*/`) |

## 7. O que não fazer

- Defaultar X-Part para assets com escadas/bandeiras/antenas.
- Ligar flap-erode watertight + fill 64 em props com frestas finas.
- Assumir que `force_close_base` com up=Y mundo é seguro pós-import glTF.
- Julgar Part3D em seed B com expectativas calibradas no seed A.
- Correr dois jobs GPU pesados (paint + part3d) na mesma 6 GB sem fila UMS estável.
