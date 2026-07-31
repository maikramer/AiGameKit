# Octree × faces — findings (simple-rpg shapes)

**Data:** 2026-07-28 · `VibeGame/examples/simple-rpg/public/assets/meshes/_intermediate/`  
**n:** 67 × `*_shape.glb` + `*.omni.json` + `sample-gameassets/manifest.yaml`  
**Manual operativo:** [`../MANIFEST_AUTHORING.md`](../MANIFEST_AUTHORING.md) §5  
**Tune code:** `Text3D/src/text3d/bbox_tune.py`

---

## 1. Método

| Campo | Fonte |
|-------|--------|
| Faces | `aigamekit_lab.glb_meta.glb_extract_meta` → `triangle_count_total` no `_shape.glb` |
| `size_m` / bbox | sidecar `*.omni.json` (fallback manifest `omni:`) |
| `category` | `manifest.yaml` |
| Octree explícito | `manifest text3d.octree_resolution` (4) ou `omni.json` (14) → **18** |
| Octree reconstruído | `tune_hunyuan_for_bbox` quality=medium, base 256, 6 GiB + `group_offload` → **49** |

Caveat: a maioria dos sidecars **não** grava `octree_resolution`; valores reconstruídos
assumem o caminho hw-auto actual. Correlação com faces reais continua válida para
tendências; budgets exactos pedem log de generate ou gravar octree no fingerprint.

---

## 2. Resultado principal

**Melhor preditor de faces ≠ octree isolado — é o tamanho mundo.**

| Preditor | Pearson vs faces |
|----------|-----------------:|
| `char_m²` | **0.87** |
| `char_m` | 0.83 |
| `octree³` | 0.59 |
| `octree²` | 0.56 |
| `octree` | 0.52 |

`char_m = (L·H·W)^(1/3)` (volume-equivalent diameter), **não** eixo maior.

Cadeia causal:

```
size_m → char_m → bbox_tune → octree → MC → faces(κ, topologia)
```

Com voxel-alvo ~constante, área física ~ `char_m²` explica faces melhor do que o
índice de grelha sozinho (que já é função de `char_m`, mas com tectos VRAM/latent
e soft-floor).

---

## 3. Fórmulas

### 3.1 Física (documentar como default)

```
faces ≈ 8.0×10⁴ × char_m²     # R² ≈ 0.65
```

| char_m | faces ~ |
|-------:|--------:|
| 0.25 m | 5k |
| 0.75 m | 45k |
| 1.5 m | 180k |
| 3 m | 720k |
| 5 m | 2.0M |
| 7 m | 3.9M |

### 3.2 Marching cubes / octree

```
faces ≈ κ × octree²
```

| Estatística κ | Valor |
|---------------|------:|
| mediana | **5.54** |
| p25–p75 | 2.39–10.86 |
| média | 8.88 |
| OLS global κ | 9.70 (R² só 0.28) |

Power fit:

```
faces ≈ 14.5 × octree^1.83
```

Quase quadrático — coerente com superfície MC ∝ resolução².

Bolso OLS (ordem de grandeza):

| octree | ~faces |
|-------:|-------:|
| 128 | 159k |
| 192 | 358k |
| 256 | 636k |
| 320 | 994k |
| 384 | 1.43M |
| 448 | 1.95M |
| 512 | 2.54M |

---

## 4. κ por categoria (mediana)

| category | n | κ med | faces med | oct med | char med |
|----------|--:|------:|----------:|--------:|---------:|
| building | 8 | 20.56 | 1.20M | 304 | 4.48 m |
| prop | 9 | 8.28 | 542k | 256 | 0.73 m |
| vegetation | 10 | 8.21 | 548k | 224 | 2.37 m |
| terrain | 11 | 8.00 | 295k | 192 | 1.96 m |
| chest | 1 | 4.37 | 219k | 224 | 0.75 m |
| humanoid | 7 | 3.32 | 310k | 256 | 0.75 m |
| item | 2 | 3.09 | 173k | 240 | 0.34 m |
| furniture | 3 | 2.59 | 130k | 256 | 0.88 m |
| creature | 11 | 2.37 | 119k | 224 | 0.78 m |
| weapon | 3 | 1.28 | 84k | 256 | 0.19 m |
| tool | 2 | 0.93 | 61k | 256 | 0.26 m |

Interpretação: κ ≈ fracção da grelha ocupada por superfície útil.
Edifício “cheio” → κ alto; espada/pickaxe no bbox → κ baixo.

---

## 5. Faces por bucket de octree

Buckets misturam categorias — **não** monótono:

| octree | n | faces mediana | κ mediana |
|-------:|--:|--------------:|----------:|
| 192 | 19 | 360k | 9.77 |
| 224 | 16 | 124k | 2.48 |
| 256 | 22 | 288k | 4.39 |
| 288 | 3 | 815k | 9.83 |
| 320 | 2 | 1.66M | 16.24 |
| 352 | 2 | 1.91M | 15.43 |
| 384 | 3 | 1.38M | 9.37 |

192 inclui muitas trees/cliffs (κ alto); 224 muitos creatures (κ baixo).

---

## 6. Âncoras

| Asset | cat | oct | src | faces | κ | char |
|-------|-----|----:|-----|------:|--:|-----:|
| village_longhouse | building | 384 | recon | 3.61M | 24.5 | 6.69 |
| chapel | building | 352 | recon | 2.64M | 21.3 | 5.74 |
| campfire_pit | prop | 256 | omni | 1.81M | 27.7 | 0.88 |
| tree_pine | vegetation | 192 | recon | 1.51M | 41.1 | 3.30 |
| hero | humanoid | 384 | mani | 315k | 2.14 | 0.70 |
| goblin | creature | 224 | recon | 51k | 1.01 | 0.57 |
| pickaxe | tool | 256 | recon | 37k | 0.57 | 0.28 |

`hero` @384 explícito: mais grelha sem char maior → κ abaixo da mediana humanoid
(lab mãos/`mc_level: 0`).

---

## 7. Regras para agentes / docs

1. Documentar **faces ≈ 8×10⁴ × char_m²** como estimativa primaria.
2. Documentar **faces ≈ 5.5 × octree²** (IQR 2.4–10.9) + tabela κ por category.
3. Happy path manifesto: `size_m` correcto; **omitir** `octree_resolution`.
4. Override octree só com comentário lab; esperar κ diferente.
5. Dobrar octree ≈ **4×** faces se topologia/κ estáveis.
6. Não usar mediana-por-bucket-octree como SLA — misturam classes.

---

## 8. Follow-ups úteis

- Gravar `octree_resolution` efectivo no sidecar Omni (ou log batch) para n
  explícito = 100% da amostra.
- Separar faces **pós** topology-fix / morph (hoje: `_shape` cru).
- Regressão: scatter `char_m²` vs faces em CI leve (sem GPU) a partir de
  sidecars + glb_meta.

---

## 9. Ajuste 2026-07-28 — props novos demasiado densos

Batch prop (16 shapes, ~19:00–19:27): mediana **471k** faces @ octree **256**
(sidecars), ~**8.9×** o orçamento `8e4·char²`.

| Asset | char | oct (sidecar) | faces | phys 8e4·c² |
|-------|-----:|--------------:|------:|------------:|
| horseshoe_pile | 0.37 | 256 | 542k | 11k |
| anvil | 0.66 | 256 | 544k | 35k |
| campfire_pit | 0.88 | 256 | 1.81M | 62k |
| desert_obelisk | 2.50 | 192 | 175k | 501k |

Causa: soft-boost `MAX=128` empurrava char&lt;1 m a soft≈220–256 **antes** da
curva geométrica (desired≈54). Piso 128 sozinho não bastava.

**Fix em `bbox_tune.py`:**

1. `_OCTREE_SMALL_BOOST_MAX` **128 → 32**
2. Tecto `octree_face_budget_cap` para props char&lt;2 m (não terrain)
3. **Piso físico** para terrain/rock: `octree ≥ √(8e4·char²/κ) × 1.125`
   — cobre overrides manuais nest 256 / cliff·outcrop 288 (approach 2 m
   fazia voxel grosso → auto 128 → buracos; +32 no manifesto era sintoma)

| Override antigo | Motivo real | Auto novo |
|-----------------|-------------|----------:|
| scorpion_nest 256 | detalhe/buracos | ≥256 |
| form_cliff_20 288 | fraco + rachas | ≥288 (+ `voxel_merge` fica) |
| form_outcrop_2 288 | idem | ≥256 (+ morph 1.5 fica) |
| hero 384 + mc_level 0 | **dedos** (não buracos) | **manual** — lab |

Alvo props: horseshoe/anvil/balde → **128–160**. Casa intacta. Removidos
`octree_resolution` de nest/cliff/outcrop no `manifest.yaml`.

---

## 10. LOD deliverable — faces + atlas por volume (2026-07-28)

`get_target_faces(category)` sozinho dava prop **24k** a um heap de 20 cm
(`horseshoe_pile` lod1 ≈ 2.8 MB). Atlas lod0/1/2 era **idêntico** na maioria
dos GLBs (path geométrico sem resize; textured com default 2048 e copy skip).

**Fix:**

| Knob | Fórmula |
|------|---------|
| LOD faces | `category_base × face_ratio × clamp((char/2)², 0.12, 1)` · piso 800 |
| Atlas lod0 | buckets paint (`paint_texture_for_char`) + **snap 64px** ∩ quality |
| Atlas lod1/2 | lod0/2, lod0/4 (snap64, piso 64) |

Código: `aigamekit_shared.lod_budget`, `get_target_faces(..., char_m=)`,
`text3d lod --texture-size`, pipeline passa volume ao stage LOD.

Horseshoe medido (pós-fix): faces **3456 / 1728 / 1152** · KTX2
**512 / 256 / 128** · ficheiros **272 KB / 95 KB / 42 KB**.

Armadilha bpy: `image.scale` + `pack` no mesmo datablock por vezes
re-empacota o JPEG original — fix = `downscale_image_replace` (nova Image
a partir dos pixels). Estudo: regenerar só `*_lod*.glb` do `_painted`.
