# Manual — configurar `manifest.yaml` (GameAssets)

Happy path para humanos e agentes: escrever um asset no manifesto, deixar o
pipeline escolher octree/faces, só override quando o lab visual exigir.

**Companheiros:**

| Doc | Quando abrir |
|-----|----------------|
| [`OMNI_SHAPE_FINDINGS.md`](OMNI_SHAPE_FINDINGS.md) | Knobs Omni, clip, presets bbox/pose, fingerprint |
| [`findings/OCTREE_FACES_FINDINGS.md`](findings/OCTREE_FACES_FINDINGS.md) | Relação empírica octree × faces (simple-rpg, n=67) |
| [`GAMEASSETS_UMS_BATCH.md`](GAMEASSETS_UMS_BATCH.md) | `gameassets batch` / waves UMS |
| [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md) | DAG Round 3, LOD0, split árvores |
| Exemplo vivo | `VibeGame/examples/simple-rpg/sample-gameassets/manifests/characters.yaml` |

Código: `GameAssets/src/gameassets/manifest.py`, `omni_ctrl.py`;
tune: `Text3D/src/text3d/bbox_tune.py`.

---

## 1. Princípio

1. **`category` + `size_m` (ou `height_m`) bastam** na maioria dos casos.
2. Soft-fill Omni preenche `control_type` / presets por categoria.
3. `bbox_tune` escolhe `octree_resolution` a partir de `char_m = (L·H·W)^(1/3)`.
4. **Não** pôr `text3d.octree_resolution` nem `octree` no `game.yaml` / payload
   UMS a menos que queiras **bloquear** o tune (override lab).

Ease over knobs: o manifesto declara *intent* (o quê, tamanho, pipeline), não a
folha de VRAM.

### O manifesto define a pasta (output_dir por grupo)

Desde que os assets se organizam em **1 manifesto por grupo** (ex. simple-rpg:
`manifests/characters.yaml`, `manifests/village.yaml`, …), o manifesto pode
declarar **no topo** (antes de `assets:`) para onde os deliverables do grupo
vão — ganham sobre o `game.yaml`:

```yaml
# manifests/characters.yaml
output_dir: ../../public/assets   # relativo À PASTA DO MANIFEST (não ao CWD)
meshes_subdir: meshes/characters  # GLBs  → ../../public/assets/meshes/characters/
images_subdir: images/characters  # PNGs  → ../../public/assets/images/characters/
assets:
  - id: hero
    …
```

Chaves suportadas (todas opcionais; omitir = herdar do `game.yaml`):

| Chave | Papel |
|-------|-------|
| `output_dir` | Raiz dos assets do grupo (resolvida relativa à pasta do manifest) |
| `meshes_subdir` | Pasta dos GLB dentro de `output_dir` (pode ter `/`, ex. `meshes/characters`) |
| `images_subdir` | Pasta dos PNG dentro de `output_dir` |
| `audio_subdir` | Pasta do áudio dentro de `output_dir` |
| `path_layout` | `split` (subdirs separados) ou `flat` (id pode ter `/`) |
| `image_ext` | `png` / `jpg` / `jpeg` |

Correr por grupo:

```bash
gameassets resume --profile game.yaml --manifest manifests/characters
# → paths: ../../public/assets/meshes/characters/hero_lod0.glb etc.
```

Notas:
- Os caminhos relativos resolvem contra a **pasta do manifesto**
  (`manifest_dir`), igual ao `output_dir` do profile — simples-rpg usa
  `../../public/assets` porque os manifests vivem em `sample-gameassets/manifests/`.
- O estado de resume (`.gameassets_work/`, `logs/`, locks) também fica junto do
  manifesto; os `_intermediate/` dos shapes ficam em `meshes/<grupo>/_intermediate/`.
- Áudio mantém-se tipicamente numa pasta única (`audio/`) — o manifesto de
  áudio não declara subdirs.

---

## 2. Anatomia de um asset

```yaml
assets:
  - id: npc_merchant          # stem dos ficheiros (meshes/npc_merchant_lod0.glb)
    idea: >-
      friendly cartoon merchant … A-pose … open hands …
    kind: character           # hint grosso (character|prop|environment|…)
    category: humanoid        # drive Omni soft-fill + quality category
    pipeline: [3d, paint, rig, animate, lod, collision]
    lod_levels: 1
    omni:
      control_type: pose
      pose_preset: quaternius-apose
      size_m: [0.65, 1.5, 0.5]   # [L=X, H=Y, W=Z] metros mundo
    animate:
      preset: humanoid
      force_preset: true
      clips: idle,walk,run,jump,attack,hit,death
```

### Campos essenciais

| Campo | Obrigatório | Papel |
|-------|:-----------:|-------|
| `id` | sim | Nome estável; sem espaços |
| `idea` | sim (shape) | Prompt Text2D / semântica 3D |
| `category` | fortemente | Soft Omni + voxel-alvo percetual + morph |
| `omni.size_m` ou `height_m` | fortemente | Escala mundo + fingerprint + **octree tune** |
| `pipeline` | sim | Stages: `3d`, `paint`, `rig`, `animate`, `lod`, `collision`, `audio` |
| `kind` | útil | Classificação grosso; softfill também usa `category` |
| `omni.control_type` | opcional | Softfill se omitido; humanoid → `pose`, props → `bbox` |
| `text3d.*` | raro | Override lab (octree, mc_level, morph…) — ver §6 |
| `animate` / `rig` | se no pipeline | Preset Quaternius; bipedes → `humanoid` + `force_preset` |

`game.yaml` define defaults globais (`generation: medium`, `text3d:`, paths).
O manifesto **por asset** ganha quando há conflito explícito.

---

## 3. Eixos `size_m` (não confundir)

Após export WebGL / VibeGame (**Y+ up**):

| Índice | Omni | Eixo | Vista de frente |
|--------|------|------|-----------------|
| 0 | **L** | **X** | largura (fachada) |
| 1 | **H** | **Y** | altura |
| 2 | **W** | **Z** | profundidade / espessura |

```yaml
# Portão: vão largo, fino em Z
size_m: [10.0, 5.5, 2.2]

# Espada: fina em Z
size_m: [0.12, 1.0, 0.04]
```

Authoring curto (coluna / árvore):

```yaml
omni:
  control_type: bbox
  height_m: 3.5
  footprint_m: 0.85   # → size_m [0.85, 3.5, 0.85] + molde Omni
```

Detalhe e armadilhas: [`OMNI_SHAPE_FINDINGS.md`](OMNI_SHAPE_FINDINGS.md) §1 e §5.

---

## 4. Escolher `category` e Omni

| category | Controlo típico | Notas |
|----------|-----------------|-------|
| `humanoid` | `pose` + `quaternius-apose` | Softfill default A-pose; `size_m` **não** injecta bbox |
| `creature` | `bbox` (explícito) | **Sem** soft pose Quaternius — declarar molde (`quadruped`, `blob`, …) |
| `prop` / `furniture` / `chest` | `bbox` | `crate` / `barrel` / `chest` / `furniture` |
| `weapon` / `tool` | `bbox` + `sword` | W = espessura (Z) pequena |
| `vegetation` / `tree` | `bbox` + `tree` | L≈W; evitar `tree` em lily/mushroom |
| `terrain` / `rock` | `bbox` | morph_close 3× (0.54 voxels) |
| `building` | `bbox` + `building`/`chapel` | `size_m` aniso **prevalece** sobre preset |

**Regras duras do batch:**

- Bipedes com clips Quaternius → `animate.preset: humanoid` + `force_preset: true`
  (não `creature`).
- `size_m` anisotrópico em modo bbox → molde Omni; não deixes `bbox_preset:
  building` engolir um arco fino.
- Mushroom / lily / bush **não** são `tree`.

---

## 5. Octree, `char_m` e faces (o que documentar)

### Cadeia

```
size_m [L,H,W]
    → char_m = (L·H·W)^(1/3)     # diâmetro por volume
    → bbox_tune → octree         # degraus 32, piso 128, tecto VRAM/latent
    → marching cubes → _shape.glb
    → faces ≈ função(topologia, octree)
```

Voxel mundo aproximado: `voxel_m ≈ char_m / octree`.
O tune mira voxel percetual (~1.2–6 cm conforme category/quality), não um
número mágico de faces.

### Fórmulas empíricas (simple-rpg, 67 shapes)

Fonte: [`findings/OCTREE_FACES_FINDINGS.md`](findings/OCTREE_FACES_FINDINGS.md).

| Modelo | Fórmula | Qualidade |
|--------|---------|-----------|
| **Física (preferida)** | `faces ≈ 8.0×10⁴ × char_m²` | R²≈0.65 · Pearson 0.87 |
| **MC / octree** | `faces ≈ κ × octree²` | κ mediana **5.5** (IQR 2.4–10.9) |
| Power-law | `faces ≈ 14.5 × octree^1.83` | quase quadrático |

**κ por categoria (mediana):**

| category | κ = faces/octree² | Ordem de faces |
|----------|------------------:|---------------:|
| building | ~20 | ~1–3M |
| prop / vegetation / terrain | ~8 | ~0.3–0.5M |
| humanoid / creature | ~2–3 | ~0.1–0.3M |
| weapon / tool | ~1 | ~40–80k |

Bolso (κ≈9.7 OLS global — ordem de grandeza, não budget):

| octree | faces ~ |
|-------:|--------:|
| 192 | 360k |
| 256 | 640k |
| 320 | 1.0M |
| 384 | 1.4M |
| 448 | 1.9M |

### Implicações para o manifesto

1. Queres **mais detalhe geométrico** → sobe `size_m` / `char_m` *ou* (lab)
   `text3d.octree_resolution`. Preferir tamanho correcto ao mundo do jogo.
2. Queres **menos faces / batch mais barato** → `generation: low|fast` no
   `game.yaml`, ou assets mais pequenos; não espalhar octree=128 à mão sem
   medir pinholes.
3. Dobrar octree com a **mesma** topologia → ~**4×** faces (κ fixo).
4. Override octree sem mudar `size_m` → κ muda (ex. `hero` @384 com char~0.7 m
   → κ≈2.1, abaixo da mediana humanoid).

Paint atlas / `_to_paint` também escalam com `char_m` — tamanho mundo puxa
custo em shape **e** paint.

---

## 6. Quando usar `text3d:` (override lab)

Bloco válido (`manifest.py` → `RowText3D`):

```yaml
text3d:
  steps: 40                 # opcional
  octree_resolution: 384    # BLOQUEIA bbox_tune
  mc_level: 0               # fingerprint; auto negativo engorda ~1 voxel
  morph_close_voxels: 0.18  # alias: voxel_merge
  # morph_close: 0.02       # metros absolutos (raro)
```

| Situação | Override |
|----------|----------|
| Dedos/mãos fundidos (hero lab) | `octree_resolution: 384` + `mc_level: 0` |
| Cliff/rocha com rachas | subir `morph_close_voxels` / category `terrain` |
| Precisas de octree exacto reproduzível | fixar `octree_resolution` + documentar no comentário |

**Anti-padrão:** copiar `octree_resolution: 256` em todos os assets “por
segurança” — mata o tune e desalinha κ.

---

## 7. Receitas mínimas

### Humanoide (NPC / hero)

```yaml
- id: npc_guard
  idea: lean cartoon guard … A-pose … open hands …
  kind: character
  category: humanoid
  pipeline: [3d, paint, rig, animate, lod, collision]
  omni:
    control_type: pose
    pose_preset: quaternius-apose
    size_m: [0.55, 1.7, 0.4]
  animate:
    preset: humanoid
    force_preset: true
    clips: idle,walk,run,attack,hit,death
```

### Prop estático

```yaml
- id: wooden_crate
  idea: low-poly wooden crate …
  kind: prop
  category: prop
  pipeline: [3d, paint, lod, collision]
  omni:
    control_type: bbox
    bbox_preset: crate
    size_m: [0.8, 0.8, 0.8]
```

### Árvore derrubável

```yaml
- id: tree_oak
  idea: tall oak tree …
  kind: environment
  category: vegetation
  pipeline: [3d, paint, lod, collision]
  omni:
    control_type: bbox
    bbox_preset: tree
    height_m: 8.0
    footprint_m: 2.5
# split-at-height: game.yaml text3d.split_at_height + heurística tree-like
```

### Building

```yaml
- id: chapel
  idea: small stone chapel eye-level three-quarter …
  kind: environment
  category: building
  pipeline: [3d, paint, lod, collision]
  omni:
    control_type: bbox
    # [L=X fachada, H=Y altura, W=Z profundidade] — aniso → molde (não só preset)
    size_m: [6.0, 7.0, 4.5]
```

---

## 8. Checklist agente (antes do `gameassets batch`)

- [ ] `id` único; paths em `game.yaml` apontam para `public/assets/…`
- [ ] `category` correcta (não `tree` em lily; não `creature` animate em bipede)
- [ ] `size_m` / `height_m` em **metros reais do jogo** (`[L,H,W]`=`[X,Y,Z]` Y+up)
- [ ] Building/arco: L=vão/fachada, W=profundidade fina se for portal
- [ ] Humanoide: A-pose + mãos abertas na `idea` (evita axila fundida)
- [ ] Sem `octree_resolution` salvo lab justificado + comentário
- [ ] Pipeline bate com intent (estático sem `rig`/`animate`)
- [ ] Após regen: olhar `_shape` (clip/pinholes), não só LOD0

Comandos:

```bash
gameassets batch --manifest manifest.yaml   # ou resume
# shape stale / Omni:
# sidecar meshes/_intermediate/{id}_shape.glb.omni.json
```

---

## 9. Onde NÃO configurar

| Sítio | Evitar |
|-------|--------|
| `game.yaml` → `octree_resolution` global | Bloqueia tune para o batch inteiro |
| Payload UMS com octree “porque 256” | Idem; deixar quality + tune |
| Runtime VibeGame / `index.html` | Nunca referenciar `_intermediate/` |
| Compensar octree baixo com weld/morph agressivo | Corrige sintoma; sobe detalhe na fonte |

---

## 10. Ver também

- Soft-fill / stale: `GameAssets/src/gameassets/omni_ctrl.py`
- Presets: `Text3D/src/text3d/omni_presets.py`
- Análise visual canvas (sessão): octree-faces-analysis (Cursor canvas)
- Lições forma Hunyuan: [`HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md)
