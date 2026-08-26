# Paint3D / Part3D — descobertas

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

---

## Paint3D (Hunyuan3D-Paint 2.1)

### O que o modelo faz

- Multi-view diffusion → bake PBR no GLB (baseColor, normal, ORM).
- Defaults alinhados demo: várias vistas @ ~640 px, **`bake_exp=6`** (menos
  ghosting entre vistas).
- Pós-bake: **bilateral** edge-preserving nas UVs (`texture_smooth`) — remove
  seams sem blur global agressivo.
- Upscale opcional Real-ESRGAN.

### VRAM / vramd

| Modo | Comportamento |
|------|----------------|
| FP16 full | Pico alto (~8 GiB ordem) — 6 GB recusa se vramd não vir quant |
| `memory_efficient=True` (interno) | SDNQ uint8 + CFG chunking + ref-UNet offload |
| Payload vramd | hw-auto / `ums_payload` preenche `memory_efficient` e/ou `sdnq_preset` (não CLI pública) |

Hardware profiles: `paint3d/hardware.py` — GPUs pequenas forçam mem-eff.

Runtime budget pós-load: clampa `max_num_view` / `view_resolution` à VRAM livre
(`PAINT3D_AUTO_VRAM_BUDGET=0` desliga).

### Knobs frequentes

| Knob | Nota |
|------|------|
| `bake_exp` | 6 default — nítidez transições vista |
| `--smooth-passes` | Passes bilateral |
| `--quality` / category | Soft via QualityEngine |
| `--gpu-ids` | Multi-GPU (env `PAINT3D_MULTI_GPU` obsoleto) |

### Prep / inpaint (interiores)

- UV/xatlas inclui **todas** as faces; bake zera peso se ângulo >~85°; `uv_inpaint`
  preenchia ilhas **nunca baked** → interiores deformados (capela).
- `paint3d/paint_prep.py`: `restrict_inpaint_mask` + monkeypatch no `painter`
  — inpaint só perto de texéis já baked.
- GameAssets: `ensure_clean_for_paint` — texturizar `_clean`, não `_shape` cru.
- Double-shell / casca plástico: resolver no shape/prompt (§ mesh lessons), não no paint.

#### Asset preto: o fill das ilhas nunca baked (2026-08-23)

Sintoma: chapel (e qualquer edifício com interior) sai quase todo **preto** — o
atlas tem fragmentos pintados e o resto é uma cor chapada escura, não zeros.

Causa: `fill_far_holes` calculava a cor de fill como a média sobre
`~far_holes`. Essa máscara inclui os buracos *near*, que naquele ponto **ainda
estão a zero** — só são inpaintados no `uv_inpaint` a seguir. Quanto menor o
bake trust, mais zeros entram na média e mais escuro fica o fill.

Medido no `chapel_to_paint.glb` com views de cinzento 128 (o atlas inteiro devia
sair 128):

| Máscara | Fração do atlas | Fill 8-bit |
|---------|-----------------|------------|
| trusted (bake real) | 5.3% | **128** ✓ |
| near-holes (por inpaintar, zeros) | 27.4% | — |
| `~far_holes` (trusted + near) | 32.7% | **21** ✗ (6.1× escuro) |

Fix: o fill vem **só de `trusted`** (`mask > 0`), e com `position_map`
(`render.uv_feature_map(vtx_pos)`) é *mesh-aware* — cada texel nunca visto herda
a cor do texel baked mais próximo **em 3D** (parede interior fica com a cor da
exterior) em vez de uma média global chapada. Sem `trusted` cai em
`_NEUTRAL_RGB`, nunca em preto.

Re-paint real da chapel: `meanlum` 21.9 → **88.0**, texels quase-pretos 76% → 5%.

> `meshVerticeInpaint` (`mesh_inpaint_processor`) **não existe** neste vendor —
> `_HAS_VERTEX_INPAINT` é `False`. Só corre o `cv2.inpaint`, que é UV-space e
> sangra entre ilhas vizinhas no atlas; daí o inpaint restrito + fill próprio.

#### O que fixa a qualidade da textura: **bake trust** (2026-08-24)

A fração do atlas com bake real manda no resultado. O resto é `cv2.inpaint`
(Navier-Stokes em UV, esborratado) ou o fill de `fill_far_holes`.

| asset | bake real | inpaint | fill | resultado |
|-------|-----------|---------|------|-----------|
| watchtower | 45.9% | 34.7% | 19.4% | nítido |
| chapel (antes) | 22.8% | 56.8% | 20.5% | esborratado |
| chapel (com bias) | **45.8%** | 51.2% | **3.0%** | nítido |

**Knobs que NÃO movem a agulha** (medidos na chapel, 13 vistas, atlas 2048):
`render_size` 1536→3072 (22.8→22.9%), `bake_angle_thres` 85→89 (22.8→23.0%),
`bake_mode: linear` (30.0→33.1% mas mais fill). `bake_mode: mip-map` está
**partido** no vendor (`MeshRender` não tem `vtx_map`).

`max_views` 6→13 dá 22.8→30.0% e satura a 13 (o greedy pára em
`max_inc > 0.01`). Numa 6 GB o orçamento (280 MiB/vista @512, fração 0.7) só
deixa 6 — subir vistas exigiria difusão multi-passagem.

#### Bake salpicado: bias de profundidade escalado pelo declive

Causa: o `back_sample` aceita um texel se `|z_texel - z_raster| < 3e-3`, com
`z_raster` amostrado no pixel **mais próximo** do raster. Numa superfície quase
de perfil esse pixel cobre muita profundidade e o texel é rejeitado como
auto-oclusão — exatamente o problema de auto-sombra dos shadow maps. Medido na
vista frontal da chapel: só **11.8%** dos texels rasterizados passam a 3e-3.

Fix: tolerância `base + slope*(1/cos - 1)`, limitada por `depth_bias_max` —
relaxa só onde o erro de amostragem é grande. `MeshRender.depth_bias_*`
(defaults = upstream, `slope=0`) + `paint_prep.install_depth_bias` (liga
`slope=0.05`; `PAINT3D_DEPTH_BIAS_SLOPE=0` desliga).

Chapel, união das vistas selecionadas:

| tolerância | 6 vistas | 13 vistas |
|------------|----------|-----------|
| `3e-3` (upstream) | 22.8% | 30.0% |
| slope 0.02 | — | 50.1% |
| **slope 0.05** | **45.8%** | 51.9% |
| slope 0.10 | — | 52.9% |

Área que alguma câmara chega a ver: **64.6%** (watchtower 77.6%). Ou seja, a
tolerância fixa deitava fora ~metade do que era visível. Com slope 0.05 e as
mesmas 6 vistas o `fill` cai de 20.5% para 3.0%.

#### Peso das vistas topo/baixo (`apply_top_view_weight`)

`candidate_view_weights` do upstream é `[1, 0.25, 0.7, 0.25, 0.05, 0.05]` —
frontal, laterais, traseira, **topo**, **baixo**. Afinado para personagens,
onde o topo é o cimo da cabeça.

Num edifício o telhado é superfície principal e com `bake_exp=6` o peso decide
quase tudo. Telhado a ~45°: vista frontal `1.0 * cos(45°)^6 = 0.125` contra
topo `0.05 * 1^6 = 0.05` na parte plana, mas `0.05 * cos(45°)^6 = 0.006` na
vertente — ou seja a vertente é pintada ~20× mais pela vista que a vê de
esguelha do que pela que a vê de frente.

`paint_prep.apply_top_view_weight(config, w)` reponderar os índices 4/5; env
`PAINT3D_TOP_VIEW_WEIGHT`. Só muda o blend — a seleção de vistas é por área
nova e o trust map não depende de pesos.

#### `normals_make_consistent` pode inverter a malha inteira (2026-08-24)

Sintoma: um asset sai muito pior que os irmãos com o mesmo pipeline
(shepherd_cottage **16.9%** de bake trust contra 45-62% dos outros).

Causa: o "recalculate outside" do Blender decide o exterior por ray casting.
Numa malha marching-cubes com milhares de ilhas e cascas interiores o
heurístico escolhe o lado errado e inverte tudo — o `back_project` zera então o
`cos_map` das faces viradas ao contrário e o bake colapsa. **É
não-determinístico**: repetir `simplify` + `topology-fix --engine arrays` com os
mesmos argumentos deu 0.0% de área invertida numa corrida e 97.7% noutra.

Área com winding invertido nos 40 maiores componentes:

| mesh | invertido | volume global | bake trust |
|------|-----------|---------------|------------|
| `shepherd_cottage_clean` | 0.1% | +12.19 | — |
| `shepherd_cottage_to_paint` | **97.7%** | **−68.39** | **16.9%** |
| `crystal_mine_entrance_to_paint` | 33.0%\* | +9.58 | 27.4% |
| `market_stall_to_paint` | 34.0%\* | +1.44 | 52.8% |
| `chapel_to_paint` | 0.2% | +26.65 | 45.8% |
| `watchtower_to_paint` | 0.0% | +2.68 | 62.5% |

\* volume global **positivo** — interior legítimo (túnel / toldo), não inversão.
Ver a nota abaixo.

Fix: `normals_consistent` (`mesh_repair.py`) confirma o **volume assinado**
(`bpy_mesh.signed_volume`, vetorizado por `foreach_get`) depois do recalc e
repõe o winding com `flip_normals` quando ficou negativo. Guarda relativa
(`OUTWARD_VOLUME_EPS` × volume da bbox) para não mexer em malhas planas/abertas,
onde o sinal não significa nada. `enforce_outward=False` mantém o comportamento
antigo.

> **Não confundir com interiores legítimos.** Um asset oco (fornalha, entrada
> de mina, banca com toldo) tem componentes cuja normal aponta para dentro
> *porque a superfície é interior* — está certo. Tentei uma guarda por
> componente (volume assinado em torno do centróide de cada componente) e ela
> vira essas faces: `forge_furnace` passou de 13% para **54%** de área
> "invertida" depois de a aplicar. A heurística por componente **não é sã** e
> foi revertida; só o sinal **global** é de confiança, porque um asset inteiro
> com volume negativo não tem leitura alternativa.
>
> Corolário: uma fração alta de "área invertida" medida por componente **não
> prova** bug — em `crystal_mine_entrance` (túnel) e `market_stall` (toldo) é
> geometria interior legítima, e o trust baixo vem de essas superfícies não
> serem visíveis de fora, tal como o interior da capela.

Diagnóstico de um asset suspeito: `trimesh` → **`m.volume` global negativo** é
o sinal de confiança. A fração de componentes com `volume < 0` serve só para
descrever a malha — num asset oco é alta e correta.

#### Cobertura de bake vs orientação da mesh

O trust map depende só de geometria + câmaras (`back_project` zera `cos_map`
quando a normal da face aponta para longe), por isso mede-se sem correr difusão:
baker com views de cor constante e ler `mask.mean()`. Números da chapel:

| Mesh | trusted | far-holes |
|------|---------|-----------|
| `village_house_to_paint` (referência sã) | 62.4% | 0.4% |
| `chapel_clean` | 27.8% | 13.0% |
| `chapel_clean` com winding invertido | **0.0%** | 99.2% |
| `chapel_to_paint` **shipped** (Ago 6) | 5.3% | 67.3% |
| `chapel_to_paint` shipped, invertido | 23.0% | 23.3% |
| `chapel_clean` → `text3d simplify` hoje | **28.7%** | 16.5% |
| … + `topology-fix --morph-close 0` (re-fix) | 28.9% | 16.0% |

Ou seja: o `_to_paint` **em disco** está com a orientação predominantemente
invertida e perde ~80% da cobertura; refazê-lo com o código atual repõe os ~28%
do `_clean`. `text3d simplify` e `topology-fix --engine arrays --morph-close 0`
não invertem winding numa esfera limpa — o ficheiro shipped é legado e
`ensure_to_paint_for_paint` reutiliza-o enquanto for mais novo que o `_clean`.
Para reprocessar: apagar o `_to_paint` (ou `force=True`) antes do paint.

### Export painted (normais / tangentes)

`paint3d.utils.mesh_io.save_glb` (2026-07-24): `smooth_shade_scene` +
`export_normals=True` + `export_tangents=True` (explícito). Painted legado
sem `NORMAL` → lod/finish import flat → V/Tri≈3 / edges vivos. Ver
[`MESH_PIPELINE_FINDINGS.md`](MESH_PIPELINE_FINDINGS.md#normais--tangentes--sobreviver-não-só-recalcular).

### Armadilhas

- Shape Omni clipado / oco → paint não “cura” geometria; só textura.
- Pedir paint sem vramd quant → fila recusa ou OOM.
- GLB painted ≠ LOD0 final se há bake-master / rig / animate a seguir.
- Painted sem `NORMAL`/`TANGENT` (export antigo) → shading partido no lod; re-paint
  ou `text3d finish` no lod (com `ktxdecompress` se já KTX2).
- **`--compile` com mem-eff/SDNQ** → FAIL (`QConv2d.weight` / Dynamo). Skip UNet
  compile; CLI pode tentar VAE só. Ver [`KERNEL_OPTS_FINDINGS.md`](KERNEL_OPTS_FINDINGS.md).

---

## Part3D (P3-SAM + X-Part)

- Decomposição semântica de mesh (partes).
- Stack SDNQ; mem-eff em 6 GB; volume decode **flashvdm** + channels_last default.
- Preferir **shape** (ou LOD limpo) como input — painted GLB pode hangar bpy
  `fix_mesh` / atrapalhar segmentação.
- Repair pós-decode: perfil `part_decode` em `aigamekit_shared.mesh_repair`.
- vramd backend `part3d` — mesmo contrato peak/quant.
- Lições finos / faces: [`../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](../HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md).

### Export (`--parts-mode`)

| Modo | Default | Nota |
|------|---------|------|
| **`faces`** | **sim** (`DEFAULT_PARTS_MODE`) | Face-split; preserva silhueta (escada/bandeira) |
| `xpart` | opt-in | Regen volumétrica — **derrete** finos / piora pés |
| `hybrid` | opt-in | X-Part + face fallback se MC falhar |

Para props com apêndices finos o entregável visual bom é **faces**, não X-Part.
`preserve_thin_topology` / exclusive partition: OFF por defeito (soldar faces+carve
em feature colada → escada dupla / buraco na parede).

### Segmentação (`--segment-mode` / `--fine-parts`)

| Preset | Segment | Observação (watchtower soldado) |
|--------|---------|----------------------------------|
| Default | `p3sam` | Quase peels **escada**; **bandeira** no corpo |
| `--fine-parts` | `hybrid` (+ refine geom.); **detail-levels forçado 0** | Peels **bandeira**; escada parte + stub soldado |

`--detail-levels 1|2` = outro eixo (re-SAM em labels grandes). Skip de parents já
thin: `hierarchical.large_region_candidates`. Peels complementares no **mesmo** N
faces: `label_fuse.fuse_protrusion_labels` (base p3sam + donor hybrid).

High-poly (>~200k): remesh 50–120k + `--segmentation-proxy` + `label_transfer`.

### Autotune anti-OOM (6 GB)

| VRAM livre | Limite |
|------------|--------|
| ≤7.5 GB | `cond_batch_size=1` |
| ≤6.5 GB | `max_parts` DiT = 1 |
| mem-eff / offload | DiT `torch.compile` **off** (VAE only) |

Conditioner **nunca** compilado (`torch_cluster.fps` × Dynamo). Conditioner OOM →
retry part-by-part. Detalhe: [`KERNEL_OPTS_FINDINGS.md`](KERNEL_OPTS_FINDINGS.md).

Não correr paint3d + part3d na mesma 6 GB em paralelo. Confirmar face count do
LOD após export (não sobrescrever `*_lod0` com clean high-poly).

---

## Changelog

| Data | Nota |
|------|------|
| 2026-08-24 | Guarda de winding: normals_make_consistent invertia a malha (shepherd_cottage 16.9% trust) |
| 2026-08-24 | Depth bias slope-scaled no bake: chapel 22.8%→45.8% de bake real (fim do esborratado) |
| 2026-08-23 | Fill das ilhas nunca baked: média só sobre `trusted` + mesh-aware (asset preto) |
| 2026-07-24 | Paint export: `NORMAL`+`TANGENT` + smooth_shade (anti edges vivos no lod) |
| 2026-07-24 | Part3D: `faces` default; p3sam vs fine-parts; `label_fuse` |
| 2026-07-24 | `restrict_inpaint` + `ensure_clean_for_paint`; interiores UV |
| 2026-07-24 | Paint compile FAIL; Part3D autotune + flashvdm/CL |
| 2026-07-19 | Extraído de ops batch + defaults Paint; payload vramd quant |
