# Descobertas sobre modelos GPU (hub)

Índice operacional das descobertas calibradas no monorepo (VRAM, kernels, Omni,
UMS, budgets). **Não** substitui READMEs por tool — aponta e consolida.

| Doc especializado | Conteúdo |
|-------------------|----------|
| [`KERNEL_OPTS_BENCH.md`](KERNEL_OPTS_BENCH.md) | Cold/hot: compile, channels_last, flashvdm (RTX 4050 6 GB) |
| [`OMNI_SHAPE_FINDINGS.md`](OMNI_SHAPE_FINDINGS.md) | Hunyuan3D-Omni: bbox max, presets, decode, falhas de batch |
| [`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md) | Admit, peak, retry VRAM, waves sem preload sync |
| [`findings/PAINT_PART_FINDINGS.md`](findings/PAINT_PART_FINDINGS.md) | Paint bake/SDNQ; Part3D input |
| [`findings/IMAGE_SKY_SOUND_FINDINGS.md`](findings/IMAGE_SKY_SOUND_FINDINGS.md) | Text2D 1024; Skymap shift/PMREM; audio trim |
| [`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md) | Master DAG, LOD0, normals, origem feet |
| [`HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md) | Pés de elefante, finos soldados, Part3D faces · [EN](HUNYUAN_MESH_AND_PARTS_LESSONS.md) |
| [`bench_omni/README.md`](bench_omni/README.md) | Pose Quaternius T-pose + smoke |
| [`ModelServer/README.md`](../ModelServer/README.md) | UMS: fila, admit, agents, CLI |
| Review vivo simple-rpg | `VibeGame/examples/simple-rpg/sample-gameassets/logs/omni_shape_inconsistencies.md` |
| [`Shared/.../vram_budget.py`](../Shared/src/gamedev_shared/vram_budget.py) | Runtime budget (chunks/views) pós-load |
| [`Shared/.../paint_budget.py`](../Shared/src/gamedev_shared/paint_budget.py) | Faces alvo pré-paint |
| [`Shared/.../lowvram.py`](../Shared/src/gamedev_shared/lowvram.py) | `FOOTPRINTS` canónicos |
| [`Shared/.../quality-profiles.yaml`](../Shared/src/gamedev_shared/data/quality-profiles.yaml) | Tiers `fast…highest` |

**Hardware de referência dos benches:** NVIDIA RTX 4050 Laptop, **6141 MiB**, 2026-07.

---

## 1. Dois orçamentos VRAM (não misturar)

```
┌─────────────────────┐     load / offload      ┌──────────────────────┐
│  UMS ADMIT (estático)│ ─────────────────────► │ RUNTIME BUDGET       │
│  pesos(quant)+act+   │                         │ 70% VRAM livre →     │
│  safety (384 MiB)    │                         │ chunks / views /tiles│
│  → aceitar ou refuse │                         │ after weights loaded │
└─────────────────────┘                         └──────────────────────┘
```

| Camada | Quando | O quê | Código |
|--------|--------|-------|--------|
| **Admit** | Antes de `ensure_loaded` | Pico estático; se pico > VRAM total → **recusa permanente** | `vram_planner`, `BackendManager.peak_vram_mib` |
| **Runtime** | Depois dos pesos | Dimensiona activação pela **livre** | `gamedev_shared.vram_budget`, `modelserver.runtime_budget` |

**Clientes SDNQ (6 GB):** enviar `sdnq_preset` e/ou `memory_efficient=true`.
Sem isso UMS assume fp16 → text3d ~8–12 GiB pico → **refuse**.

Helpers: `with_ums_peak_opts` / `with_ums_load_opts` em `gamedev_shared.cli_helpers`.

---

## 2. Footprints canónicos (`FOOTPRINTS`)

Valores em GiB (fp16 weights / activation / largest leaf). Admit aplica factor
SDNQ aos pesos; `memory_efficient` pode reduzir activação (~×0.65).

| Key | Pesos | Act | Largest | Uso |
|-----|------:|----:|--------:|-----|
| `hunyuan3d-omni` | 10.0 | 2.0 | 6.0 | Text3D Omni |
| `hunyuan-paint` | 6.0 | 2.0 | 5.0 | Paint3D |
| `hunyuan3d-part` | 4.75 | 1.5 | 5.2 | Part3D |
| `sana-sprint-600m` | 7.3 | 1.5 | 3.0 | Text2Icon |
| `flux-dev-uint4` | 7.4 | 2.0 | 3.0 | Skymap (já quant) |
| `flux-klein-4b` | 14.0 | 1.5 | 5.0 | Text2D 4B |
| `stable-audio-open` | 3.5 | 1.5 | 2.0 | Text2Sound |

**Factors SDNQ (runtime, não checkpoint pré-quant):**

| Preset | × pesos (aprox.) |
|--------|------------------|
| `sdnq-int4` | 0.32 |
| `sdnq-uint8` / int8 | 0.55 |

**Group offload (text3d típico c/ SDNQ):** pico ≈ `largest(quant)` + activação
(full); chunks orçados no runtime budget depois.

**Nota:** tabela “VRAM MiB” do ModelServer README é estimativa YAML; **admit real**
usa `FOOTPRINTS` + quant. text3d YAML ~10000 MiB / footprint Omni 10+2 GiB fp16.

---

## 3. Runtime budget — números calibrados

| Param | Valor | Notas |
|-------|------:|-------|
| Fração VRAM livre | **0.70** | `DEFAULT_VRAM_FRACTION` |
| Text3D bytes/query | **96 KiB** | env `TEXT3D_DECODE_BYTES_PER_QUERY` |
| Text3D chunks | **8192…524288** | clamp |
| Paint MiB/view @512 | **~280** | ~6 views com 1.5–2 GiB livres |
| Paint views | **2…10** | |
| DINO GPU min free | **1.6 GiB** | senão DINO→CPU |
| MeshRender min free | **256 MiB** | dual-UNet deixa ~34 MiB → OOM |
| Livre &lt; 2.5 GiB | — | cfg_chunk + ref_offload + DINO CPU |

Observabilidade: resposta `runtime_budget`; `ums stats` → `last_runtime_budget`.
Kill-switch paint: `PAINT3D_AUTO_VRAM_BUDGET=0`.

---

## 4. Paint budget (malha `_to_paint`)

Paint escala com **faces**, não com texels UNet.

| Constante | Valor |
|-----------|------:|
| UV packing | 0.55 |
| Texels/face alvo | 10 |
| Faces | **12k…160k** (clamp) |
| Fórmula | `faces ≈ texture_size² × packing / texels_per_face` |

**Do:** `text3d simplify` / Decimate para face budget.  
**Don't:** `remesh` voxel como substituto de simplify pré-paint.

---

## 5. Kernel opts — vencedores (6 GB)

Resumo de [`KERNEL_OPTS_BENCH.md`](KERNEL_OPTS_BENCH.md). Prompt/seed fixos.

| Tool | Usar | Evitar |
|------|------|--------|
| **Text3D** | `flashvdm` (−42% hot vs vanilla) | fp8 (Half/BF16); compile one-shot; CL só (~0) |
| **Text2D** | compile+CL em **batch/UMS** | step-cache c/ group_stream |
| **Text2Icon** | channels_last batch | compile em 6 GB (hot pior) |
| **Paint3D** | mem-eff / SDNQ | `--compile` mem-eff (`QConv2d` FAIL) |
| **Part3D** | flashvdm+CL; `cond_batch=1` `max_parts=1` ≤6.5 GB | Conditioner compile (`torch_cluster.fps`) |
| **Skymap2D** | compile em batch/UMS (−19% hot) | CL só (~0); one-shot compile (cold longo) |
| **Texture2D / Text2Sound** | baseline | compile/CL (sem ganho nestes benches) |

Bottleneck Text3D fast em 6 GB: **volume decode vanilla** (~2122 chunks), não DiT.

Defaults já aplicados (UMS/batch): ver secção “Aplicado” em KERNEL_OPTS_BENCH.

---

## 6. Hunyuan3D-Omni (ponteiro)

Detalhe completo: [`OMNI_SHAPE_FINDINGS.md`](OMNI_SHAPE_FINDINGS.md).

**Hard rules:**

1. **Dois espaços:** bbox Omni = aspect (eixo maior = 1.0); MC ≈ ±1.08.
2. **`OMNI_BBOX_AXIS_MAX` deve ser 1.0** — valor 2.0 = clip planar + GLBs enormes.
3. Exactamente **um** `control_type` por forward (`bbox` \| `pose` \| …).
4. **`size_m` não injecta bbox** com `control_type=pose` (engorda).
5. Humanoids → `pose_preset` T-pose; props → `bbox_preset` correcto (não `tree` em mushroom).

Smoke pose: [`bench_omni/`](bench_omni/) — ~3.1 GB VRAM c/ SDNQ int4 + flashvdm.

---

## 7. Quality tiers (mapa rápido)

`QualityEngine` = soft fill (não sobrescreve flags explícitas).

| Tier | text3d.preset (típico) | paint views / view_res / texture (típico) |
|------|------------------------|-------------------------------------------|
| fast | fast | 2 / 384 / 1024 |
| low | fast | 4 / 384 / 2048 |
| medium | balanced | ver YAML |
| high / highest | hq | 8 / 512 / 4096 |

Text3D CLI presets (steps/octree/chunks): ver `Text3D/README.md` — `fast` 18/128/4096,
`balanced` 24/256/8000, `hq` 30/384/20000.

**hw-auto Text3D (VRAM):** ≥10 GB hq; 7.5–10 balanced; **5–7.5 balanced+SDNQ int4**;
&lt;5 fast+int4. FlashVDM preferido se VRAM&lt;7.5 GiB (bench).

---

## 8. UMS + GameAssets — co-op batch

### Orquestração

| Antes | Agora |
|-------|-------|
| Subprocess `generate-batch` → `delegate_to_ums` sync (fila≈1) | `ums_coord` / `ums_batch`: **submit×N → wait** (1º job carrega backend) |
| Preload sync text3d/paint antes da wave | **`preload=False`** — load Omni pode >10 min; timeout client 600 s → Broken pipe → evict → fila VRAM stuck |
| Master pipeline no meio da wave paint | **Defer** `MasterDeferQueue` até fim da wave GPU |
| Só `GAMEDEV_UMS_PRIORITY=batch` | Flags `--no-ums` / `--ums-stream`; env affinity/inflight |
| VRAM transitória = fail job | Evict + backoff + **requeue** até `GAMEDEV_UMS_MAX_VRAM_RETRIES` |

Ordem fixa (anti-thrash text3d↔paint3d):

```
shapes (text3d sobe no 1.º job) → ensure_to_paint (CPU)
→ paints (paint3d sobe no 1.º job) → drain master → simplify/catch-up
```

Código: `GameAssets/src/gameassets/ums_coord.py`, `ums_batch.py` (`preload=False` shape/paint).
Payloads: `text3d.ums_payload`, `paint3d.ums_payload`.
Detalhe ops: [`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md).

### Agents — GPU ocupada

1. `gamedev-model-server status` / `queue` / **`debug`**
2. Esperar (`ums wait`, `--ums-stream`) ou `ums cancel`
3. **Nunca** `kill` / `nvidia-smi` pkill / `--gpu-kill-others` com UMS busy
4. `queue_full` / timeout UMS up → **sem** fallback in-process paralelo

### CLI diagnóstico (read-only)

| Cmd | Uso |
|-----|-----|
| `… debug` | HOLDING + fila + erros + budgets + GPU; `--watch N` |
| `… stats` | backends + queue p50/p95 + `last_runtime_budget`; `--json` |
| `… stats --reset` | Zera **contadores** (não para UMS / não cancela jobs) |
| `… bench` | RTT IPC only (`status`/`queue`/`stats`) — **sem** generate |

Supervisor já a correr pode não ter código novo até restart **voluntário**.

---

## 9. Mesh / export (Text3D dono)

| Descoberta | Regra |
|------------|-------|
| MC → paredes duplas, rachas | `mesh_repair` profiles: `topology_clean`, `pre_decimate_uv`, … |
| Normais | **Não** `normals_split_custom_set` → V/Tri=3, ficheiros inchados |
| Origem | Pipeline default **feet**; rotação Hunyuan→OpenGL em **todas** as stages |
| LOD0 | Entregável terminal: animated > rigged > painted |
| Pés de elefante / finos soldados | [`HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md`](HUNYUAN_MESH_AND_PARTS_LESSONS_PT.md) |

Ver `Text3D/AGENTS.md`, `gamedev_shared.mesh_repair`,
[`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md).

---

## 10. Inconsistências conhecidas (docs vs código)

| Item | Estado |
|------|--------|
| ModelServer README text3d **8000** MiB vs YAML/Omni footprint **~10 GiB** | Admit usa `FOOTPRINTS`; coluna README = legado — preferir esta hub + `lowvram.py` |
| README hw-auto “hierarchical” vs bench **flashvdm** &lt;7.5 GiB | Preferir flashvdm em 6 GB (KERNEL_OPTS) |
| `sdnq.suggest_preset_for_vram` (≥6 GB → uint8) vs UMS 6 GB → **int4** text3d | Tools/UMS path: int4 em ~6 GB para text3d |

---

## 11. Checklist rápido (6 GB)

```
[ ] sdnq_preset / memory_efficient nos payloads UMS
[ ] Text3D: flashvdm + int4; Omni bbox_axis_max=1.0
[ ] Paint: face budget 12k–160k; sem compile+SDNQ
[ ] Batch GameAssets: UMS up; prioridade batch; não kill mid-queue
[ ] Debug: ums debug / stats — não nvidia-smi pkill
[ ] One-shot CLI: compile off (excepto defaults batch documentados)
```

---

## 12. Onde calibra / atualiza

| Alterar | Ficheiro |
|---------|----------|
| Footprint modelo | `Shared/.../lowvram.py` `FOOTPRINTS` |
| Bytes/query decode | `vram_budget.py` + env |
| Faces paint | `paint_budget.py` |
| YAML admit hint | `ModelServer/.../backends.yaml` |
| Bench tabela | `docs/scripts/bench_kernel_opts.py` → KERNEL_OPTS_BENCH |
| Omni clip/presets | `OMNI_SHAPE_FINDINGS.md` + `omni_controls.py` |

Ao mudar defaults hw-auto ou UMS wave kwargs: actualizar **esta hub** +
KERNEL_OPTS “Aplicado” + `findings/*` relevantes na mesma alteração.

### Changelog hub

| Data | Nota |
|------|------|
| 2026-07-19 | Índice `findings/*`; preload=False shape/paint; VRAM requeue; links Hunyuan lessons + review simple-rpg |
