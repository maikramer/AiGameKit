# Hunyuan3D-Omni — descobertas, knobs e armadilhas

Hub de todos os modelos: [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md).

Notas operacionais do monorepo (Text3D + GameAssets) após regeneração / review visual
do batch `simple-rpg` (2026-07). Complementa a tabela de flags em
[`Text3D/README.md`](../Text3D/README.md#omni-geometric-controls) e o bench de pose em
[`docs/bench_omni/`](bench_omni/README.md).

**Hub VRAM / kernels / UMS (todos os modelos):** [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md).

**Review por asset (vivo):**  
`VibeGame/examples/simple-rpg/sample-gameassets/logs/omni_shape_inconsistencies.md`

**Código canónico:**

| Área | Path |
|------|------|
| Escala bbox / cantos | `Text3D/src/text3d/utils/omni_controls.py` |
| Presets pose/bbox + fingerprint | `Text3D/src/text3d/omni_presets.py` |
| Bounds MC / mc_level / chunks | `Text3D/src/text3d/decode_tune.py` |
| Soft tune octree/steps por tamanho | `Text3D/src/text3d/bbox_tune.py` |
| Manifest `omni:` → CLI | `GameAssets/src/gameassets/omni_ctrl.py` |
| Assets bone/GLB | `Text3D/src/text3d/data/omni/` |

**Manual manifesto (authoring):** [`MANIFEST_AUTHORING.md`](MANIFEST_AUTHORING.md) —
`category` + `size_m`, quando (não) override `octree_resolution`.

**Octree × faces (empírico simple-rpg):** [`findings/OCTREE_FACES_FINDINGS.md`](findings/OCTREE_FACES_FINDINGS.md)
— `faces ≈ 8×10⁴ · char_m²`; `faces ≈ κ · octree²` (κ mediana ≈ 5.5).

---

## 1. Três espaços de coordenadas (não confundir)

```
Hunyuan raw:        +Z up (marching cubes / Omni forward)

Export Text3D:      X+90° → WebGL / glTF / Three.js / VibeGame exhibit
                    Y+ up  (confirmado no exhibit e em sword_hero_shape)

Omni bbox / size_m: aspect ou metros [L, H, W]
Marching cubes:     volume ~[-box_v, +box_v]  (default box_v ≈ 1.08)
                    grid isotrópico em células; bounds anisotrópicos
                    redistribuem resolução por eixo (decode_tune)
```

### Mapa canónico `size_m` / bbox → WebGL (após export)

| Índice | Nome Omni | Eixo WebGL | Vista de frente (câmara em −Z) |
|--------|-----------|------------|--------------------------------|
| 0 | **L** | **X** | esquerda ↔ direita (largura da fachada / vão) |
| 1 | **H** | **Y** | baixo ↔ cima (**Y+ up**) |
| 2 | **W** | **Z** | perto ↔ longe (profundidade / espessura) |

Prova empírica (`sword_hero_shape.glb`): `size_m=[0.12, 1.0, 0.04]` → extents
XYZ ≈ `[0.13, 1.0, 0.05]`. Porta/arco finos = **W pequeno** (Z), não L.

**Armadilha de naming:** comentários antigos chamavam L de “length/profundidade”.
Em WebGL, **profundidade = W (Z)**. L é a largura lateral (X).

Exemplos de authoring:

```yaml
# Portão largo 10 m, alto 5.5 m, fino 2.2 m (vão na fachada)
size_m: [10.0, 5.5, 2.2]   # [L=X, H=Y, W=Z]

# Casa mais profunda que larga (entra pela porta)
size_m: [5.0, 4.2, 7.0]    # largura 5, altura 4.2, profundidade 7
```

- **Bbox Omni = aspect ratio**, não metros mundo.
- **`size_m` anisotrópico em modo bbox** → molde Omni (`size_m_to_bbox`);
  **prevalece sobre `bbox_preset`** (senão `building` 0.86×1×0.64 engole um
  arco 10×5.5×2.2 — visto em `city_gate_arch_shape`).
- **Escala pós-mesh** = uniforme no eixo maior (`max(size_m)`); não estica
  L/H/W por separado. O aspect tem de vir do molde Omni.
- **MC = volume de decode**. Se a geometria (ou cantos do controlo) enchem até aos
  planos do MC → **clip planar** (faces cortadas a régua).
- Imagem de referência + prompt/`idea` guiam semântica; o controlo geométrico só
  ancora pose/aspect/volume.

---

## 2. Bug crítico: `OMNI_BBOX_AXIS_MAX`

| Valor | Efeito |
|-------|--------|
| **`1.0` (correcto)** | Eixo maior da bbox = 1 → cantos ±0.5 → margem face ao MC ±~1.08 |
| **`2.0` (errado)** | Cantos ±1 → enche o cubo MC → clip em todos os lados + GLBs enormes (field cheio) |

Constante: `OMNI_BBOX_AXIS_MAX` em `omni_controls.py`.  
Fingerprint inclui `bbox_axis_max`; sidecars **sem** a chave = legado max=2 →
`omni_fingerprint_matches` força stale (regen).

**Sinal visual do clip clássico (max=2):** planos ortogonais, silhueta “cortada na
caixa”, pine/mushroom ~300 MB achatados.  
**Pós-fix (max=1):** maioria dos shapes sem esses planos; falhas restantes são
eixo longo residual, semântica, ou buracos SDF.

---

## 3. Knobs — mapa rápido

### Controlo Omni (exactamente **um** por forward)

| Knob | Onde | Notas |
|------|------|--------|
| `control_type` | manifest / `--control-type` | `none` \| `bbox` \| `pose` \| `point` \| `voxel` |
| `pose_preset` | `--pose-preset` | `quaternius-tpose`, `quaternius-tpose-dwarf`, `quaternius-apose` (+ aliases) |
| `pose_file` | `--pose-file` | bone.txt custom (51×6) |
| `bbox_preset` | `--bbox-preset` | ver tabela abaixo |
| `bbox` / `size` | `--bbox` / `--size` | aspect L,H,W (3) ou AABB (6) |
| `size_m` | `--size-m` / `omni.size_m` | **metros mundo** `[L,H,W]`→`[X,Y,Z]` Y+up. Com **pose**: só escala (não injecta bbox). Com **bbox** + size_m **aniso**: molde Omni (`size_m_to_bbox`), prevalece sobre `bbox_preset`. Escala pós-mesh = uniforme no eixo maior. |
| `height_m` + `footprint_m` | `--height-m` / `--footprint-m` / manifest | Authoring: altura (+ footprint L=W). Expande `size_m`. Em **modo bbox** o aspect vira **molde Omni** (modelo enche) — não é só escala pós-mesh |
| `point_cloud` / `point_from` | CLI / manifest | âncora de forma; `point_from` = sibling asset id |
| `voxel_mesh` | CLI | âncora de volume/blockout |

### Decode / qualidade de malha

| Knob | Default / modo | Papel |
|------|----------------|--------|
| `bounds_mode` | `auto` | Bounds anisotrópicos a partir da bbox (`bounds_for_bbox`) |
| `DEFAULT_BOX_V` | `1.08` | Meia-extensão do cubo clássico MC |
| `BOUNDS_REL_MARGIN` | `0.12` | Folga nos eixos curtos (anti-clip) |
| `BOUNDS_ABS_SLACK` | `0.05` | Folga absoluta |
| `BOUNDS_EDGE_KEEP` | `0.04` | Eixo longo **nunca** enche `box_v` até ao plano MC |
| `BOUNDS_MIN_AXIS_FRAC` | `0.08` | Piso eixo fino (0.20 engordava espadas → bastão) |
| `mc_level` | `auto` | Iso ligeiramente negativo ∝ 1/octree (fecha pinholes) |
| `morph_close_voxels` / `voxel_merge` | `0.18` (terrain/rock=`0.54`) | N do fecho morfológico («voxel merge») no topology-fix; 3× em cliffs/rochas |
| `num_chunks` | auto VRAM | Batch do geo-decoder pós-offload |
| `octree_resolution` / steps | quality + `bbox_tune` | Soft por `size_m` / category |
| `volume_decoder` | `flashvdm` típico | Preferir surface-focused em octree alto |
| `sdnq_preset` | `sdnq-int4` em 6 GB | UMS peak; omitir → assume fp16 e pode recusar |

### Resume / invalidação

Sidecar: `<shape>.glb.omni.json` via `omni_fingerprint()`.

Campos que invalidam: `control_type`, preset/bbox efectivo, `size_m`, `seed`
(override explícito), `bounds_mode`, `mc_level`, **`bbox_axis_max`**.

Seed determinístico (hash) **não** entra no fingerprint (evita stale em massa).

---

## 4. Presets bbox — o que usar

Definidos em `BBOX_PRESETS` (`omni_presets.py`). Eixo maior = 1.0.

| Preset | Aspect (L,H,W) | Usar para | Evitar em |
|--------|----------------|-----------|-----------|
| `humanoid` | 0.45, 1, 0.35 | bipede adulto (só bbox; preferir pose) | |
| `humanoid-child` | 0.71, 1, 0.57 | chibi / ombros largos | |
| `quadruped` | 1, 0.55, 0.4 | lobo, cavalo, … | bipedes |
| `blob` / `slime` | 1, 1, 1 | isótropo (bola); evitar L≠W («carro») | aspect achatado |
| `tree` | 0.55, 1, 0.55 | tronco cilíndrico L=W | `0.35` antigo → papel fino de perfil + galhos fio |
| `column` / `cactus` | 0.4, 1, 0.4 | coluna / saguaro (mais fino que tree) | copa larga |
| `flat` / `flying` | 1, 0.4375, 1 | lily pad, insecto voador | `tree` |
| `building` / `chapel` | 0.86, 1, 0.64 | casas: L=largura fachada (X), W=profundidade (Z) | props pequenos |
| `sword` | 0.12, 1, 0.04 | armas/ferramentas: W=espessura (Z) | |
| `cube` / `crate` | 1,1,1 | caixas; **perigoso** em organic | slime, shade, mosquito |
| `chest`, `barrel`, `furniture`, `door`, `shield` | … | props tipados | |

Soft defaults por `category` (`CATEGORY_OMNI_DEFAULTS`): humanoid → pose
**A-pose** (`quaternius-apose`); vegetation/tree → bbox `tree`; weapon/tool →
`sword`; etc.  
Categoria `creature` **não** tem soft pose (quad ≠ Quaternius) — manifesto
explícito obrigatório.

**Regra dura vista no batch:**  
`mushroom` / `lily_pad` / `dead_bush` como `tree` → aspect errado + clip/achatar.  
`slime` com aspect L≠W → «carro»; blob isótropo (1,1,1). `tree` 0.35 → papel fino.  
`city_gate` com `bbox_preset: building` + `size_m` aniso → preset ganhava o molde
(arco saía “largo/gordo” em vez de vão largo × fino em Z) — corrigido:
`size_m` aniso prevalece.

### Pose presets

| Preset | Quando |
|--------|--------|
| `quaternius-tpose` | humanoid adulto → rig SkinTokens |
| `quaternius-tpose-dwarf` | goblin/chibi/cabeça grande |
| `quaternius-apose` | default humanoid; braços -45° (T-pose estica ombros/mãos — merchant/ogre) |
| `quaternius-apose-dwarf` | chibi / goblin / cabeça grande |

---

## 5. `size_m` / `height_m` vs bbox Omni

**Eixos (repetir até ficar automático):** após export WebGL, `size_m=[L,H,W]` ≡
`[X, Y, Z]` com **Y+ up**. Largura de fachada = L (X); profundidade = W (Z).

| | `size_m` / `height_m` | `bbox` / `bbox_preset` |
|--|----------|-------------------------|
| Unidade | metros mundo | aspect 0–1 |
| Controlo Omni | **sim** se `control_type=bbox` e size_m **aniso** (molde via `size_m_to_bbox`); **não** com pose | **sim** — o modelo **enche** este volume |
| Resume | sim (fingerprint via `size_m`) | sim |
| Soft tune | octree/steps (`bbox_tune`) | — |
| Escala pós-mesh | uniforme no **eixo maior** (`max(size_m)`) | — |

**Omni não gera “em metros”.** Não há knob métrico no forward: a rede condiciona
por imagem + pose/bbox/point/voxel. A bbox é um **molde de aspect** (soft) —
conteúdo tende a preencher a caixa; caixa demasiado fina → papel/esticado;
caixa larga demais → blob gordo. Escala pós-mesh **não** corrige proporções
erradas (só o tamanho mundial).

**Prioridade do molde (bbox mode):**

1. `bbox` / `--size` explícito (aspect cru)
2. `size_m` **anisotrópico** ou `height_m`+`footprint_m` → `size_m_to_bbox`
3. `bbox_preset` (ex. `building`) — **só** se size_m ausente ou quase cúbico

`city_gate_arch` com `size_m=[10,5.5,2.2]` + `bbox_preset: building` sem a regra 2
ficava com molde `[0.86,1,0.64]` e extents ~`[8.6, 6.5, 10]` (gordo em Z).
### Authoring preferido (props / vegetação / animados em bbox)

```yaml
omni:
  control_type: bbox
  height_m: 3.5      # altura desejada
  footprint_m: 0.85  # L=W → molde coluna; Omni gera geometria neste aspect
```

Isto expande `size_m=[0.85,3.5,0.85]` **e** injecta bbox normalizado
(`size_m_to_bbox`) para o modelo — não é “só escalonar o mesh”.  
Com `pose` (humanoides): `height_m` só → `size_m` mundo; esqueleto manda na forma.

Armadilha antiga: tratar `size_m` como “explicit geom” → bloqueava soft-fill de
pose e injectava bbox → personagens engordavam a preencher a caixa.  
Hoje: `size_m` sozinho **não** bloqueia soft-fill de pose em `merge_omni_controls`
(`has_geom`). Em **modo bbox**, size_m **aniso** (ou height+footprint) **sim**
vira molde via `size_m_to_bbox` e limpa `bbox_preset` — não confundir as duas regras.

Excepções bbox: `height_m`+`footprint_m` em modo bbox → molde do aspect;
`cube`+`size_m` não-cúbico → aspect de `size_m`; size_m aniso + qualquer
`bbox_preset` → aspect de `size_m`.

---

## 6. Falhas observadas (pós max=1) — classes

### A. Clip residual no eixo longo

Sinal: topo/base **plano a régua**; `world_bounds` com eixo longo ≈ constante
cheia (ex. `tree_oak` Z≈8.0).

Hipótese: aspect `tree` + `size_m` alto + decode ainda a aproximar o plano MC
apesar de `BOUNDS_EDGE_KEEP`. Estudar: margem maior no preset tree, ou reduzir
altura efectiva no controlo, ou `box_v`/edge_keep.

Vistos: `tree_oak` (REGEN), `witch_hut` topo (?), bases planas em
`mushroom_red` / `rock_mossy` / `dead_tree` (pode misturar “pés no chão” com clip).

### B. Semântica (controlo/pose ≠ espécie)

Omni âncora esqueleto/aspect; a imagem/`idea` falha → criatura errada.

| Asset | Esperado | Obtido |
|-------|----------|--------|
| wolf | quadruped | humanoide fox |
| sand_wyrm_boss | serpente/wyrm | sapo bípede |
| moss_rock | pedra | blob cogumelo |
| scorpion_nest | ninho | tenda cónica |

Mitigações a estudar: `bbox_preset=quadruped` + idea reforçada; pose custom
quad; `point`/`voxel` de blockout; negative prompt; seed re-roll (`seed:` no
manifest → fingerprint).

### C. Topologia / buracos

`village_longhouse`: frente/base oca (≠ open-base de torre). Estudar hole-fill
pós-shape (`topology-fix`) vs regen.

### D. Artefactos Omni “normais”

- Dedos fundidos (`hero`)
- Braços gordos / costuras manga (`npc_merchant`) — tentar A-pose
- Folhagem gorda / ramos tubulares (`pine_dark`, `dead_bush`)
- Disco fino na base de árvores mortas (`dead_tree`, `dead_willow`)
- Armas `WARN_FLAT` nos bounds → **esperado**, não é clip MC
- GLB HI 100–190 MB (chapel, shack, well, pillar) → esperado no `_shape`;
  `bake-master` deve reduzir

### E. Base aberta intencional

`watchtower` visto de baixo = oco OK. Não confundir com buraco de longhouse.

---

## 7. Operação batch / UMS (relevância Omni)

- Shape wave: **não** pré-carregar text3d sync longo (timeout → Broken pipe →
  evict → fila VRAM stuck). 1º job carrega o shape certo.
- Pico VRAM: payload UMS com `sdnq_preset` / `memory_efficient` (hw-auto / `resolve_*_vram_opts` — não CLI `--low-vram`).
- Erro VRAM transitório: UMS requeue + backoff (`GAMEDEV_UMS_MAX_VRAM_RETRIES`).
- **Nunca** kill GPU enquanto UMS tem jobs.
- Resume: intermediários em `_intermediate/`; fingerprint Omni tem de bater
  senão regenera shape.

---

## 8. Checklist antes de gerar / regenerar

1. `control_type` correcto (pose humanoid vs bbox prop/creature).
2. Preset aspect coerente com silhueta (não `tree` em squat; não `cube` em blob).
3. Humanoid musculado/gordo → considerar `quaternius-apose`.
4. Quadruped / serpente / ninho → não confiar só em T-pose + idea vaga.
5. Eixos: `size_m=[L,H,W]` → WebGL `[X,Y,Z]` Y+up (L=largura, W=profundidade).
   Pose: `size_m` = só metros. Bbox + size_m **aniso**: molde Omni (ganha ao preset).
6. Após mudar `OMNI_BBOX_AXIS_MAX` / presets / decode knobs fingerprintados →
   shapes antigos stale (ok).
7. Validar visual: 3 views (`front,three_quarter,right`) + bounds; plano a régua
   no eixo longo = suspeito. `_shape` com altura em Z (não Y) = rotação de export em falta.
8. Só depois: paint / rig / animate (LOD0 = estágio terminal correcto).

---

## 9. CLI mínimo

```bash
# Humanoid rigável
text3d generate -i hero.png --pose-preset quaternius-tpose \
  --size-m 0.55,1.55,0.4 -o hero_shape.glb --sdnq-preset sdnq-int4

# Prop / arma
text3d generate -i sword.png --bbox-preset sword -o sword_shape.glb

# Blob / vegetação baixa
text3d generate -i mushroom.png --control-type bbox --bbox-preset blob \
  -o mushroom_shape.glb

# Aspect a partir de metros (modo bbox)
text3d generate -i prop.png --control-type bbox --size-m 2,1,1.5 \
  -o prop_shape.glb
```

Manifest (GameAssets):

```yaml
omni:
  control_type: pose
  pose_preset: quaternius-apose
  height_m: 1.55          # só metros mundo; pose manda na forma
# ou props — molde que o modelo preenche:
omni:
  control_type: bbox
  height_m: 3.5
  footprint_m: 0.85
# ou
omni:
  control_type: bbox
  bbox_preset: quadruped
  # L=comprimento corpo no eixo X pós-export; H=altura; W=largura transversal (Z).
  # Se o jogo faz a criatura olhar −Z, pode ser preciso yaw 90° no spawn — o molde
  # Omni não rodopia com a facing do NPC.
  size_m: [1.4, 0.8, 0.6]
```

### Soft-fill por categoria (batch) — contrato GameAssets

Ordem típica em `resolve_row_omni` / shape wave:

| API (`omni_ctrl`) | Papel |
|-------------------|--------|
| `omni_from_dict` / `merge_omni` | Profile + row |
| `softfill_omni_from_category` | Se **não** há controlo geométrico activo → defaults de categoria (ex. humanoid → pose; vegetação → bbox tree). Preferência: `text3d.omni_presets.CATEGORY_OMNI_DEFAULTS`. Se Text3D **não** está instalado (CI GameAssets-only), usa `_CATEGORY_OMNI_DEFAULTS_FALLBACK` em `omni_ctrl.py` — **não** devolver Omni intacto / no-op. **Não** sobrescreve Omni já definido. `size_m` sozinho **ainda** recebe soft-fill (senão fica só escala) |
| `expand_omni_world_size` | `height_m` / `footprint_m` → `size_m` |
| `prepare_shape_for_generation` | Decide regen vs reuse; escreve sidecar |
| `shape_omni_stale` | Fingerprint sidecar `*_shape.omni.json` vs pedido actual |
| `omni_to_cli_flags` / `omni_to_batch_item` | CLI subprocess / payload UMS |

Stale: sem sidecar **não** apaga mesh no resume (usar `--force` se mudaste
Omni sem sidecar). Payload UMS: `text3d.ums_payload.build_generate_request`
(campos Omni + `seed_fingerprint` / `bbox_tune`; omitir `octree_resolution`
quando o soft tune size-based manda).

Código: `GameAssets/src/gameassets/omni_ctrl.py` · testes
`tests/test_omni_softfill.py` · waves: [`GAMEASSETS_UMS_BATCH.md`](GAMEASSETS_UMS_BATCH.md).

---

## 10. Trabalho em aberto (estudo → depois regen)

1. Clip +Y em árvores altas (`tree_oak`) — edge_keep / preset / size_m.
2. Presets ou poses para quadruped / serpent / nest (ou point-from blockout).
3. Hole-fill agressivo em edifícios ocos vs regen.
4. Disco-base árvores mortas — topology-fix / origem feet.
5. Merchant A-pose + idea; wolf quadruped explícito.
6. Documentar na review log cada `form_*` / `boss_ogre` quando o batch acabar.

---

## Changelog doc

| Data | Nota |
|------|------|
| 2026-07-24 | Softfill: fallback `_CATEGORY_OMNI_DEFAULTS_FALLBACK` sem pacote Text3D (CI) |
| 2026-07-24 | Contrato API softfill / prepare / stale + link UMS batch |
| 2026-07-24 | Soft-fill Omni por categoria + stale sidecar no batch |
| 2026-07-19 | 1ª versão: max=1 vs 2, knobs, presets, falhas simple-rpg, UMS wave |
| 2026-07-19 | `height_m`+`footprint_m` = molde bbox (modelo enche); preset `column`/`cactus` |
