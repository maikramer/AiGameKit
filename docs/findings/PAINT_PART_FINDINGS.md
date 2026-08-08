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
| 2026-07-24 | Paint export: `NORMAL`+`TANGENT` + smooth_shade (anti edges vivos no lod) |
| 2026-07-24 | Part3D: `faces` default; p3sam vs fine-parts; `label_fuse` |
| 2026-07-24 | `restrict_inpaint` + `ensure_clean_for_paint`; interiores UV |
| 2026-07-24 | Paint compile FAIL; Part3D autotune + flashvdm/CL |
| 2026-07-19 | Extraído de ops batch + defaults Paint; payload vramd quant |
