# GameAssets — vramd batch waves (operador)

Happy path do `gameassets batch` quando a GPU passa pelo **Unified Model Server**.
Complementa [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md) §8 e
[`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md).

**Código:** `GameAssets/src/gameassets/ums_coord.py`, `ums_batch.py`,
`batch_cmd.py` · **Omni soft-fill:** `omni_ctrl.py` · **DAG pós-GPU:**
[`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md) (Round 3).

---

## Modelo mental

1. Batch agrupa jobs GPU em **waves** (mesmo backend) → `run_gpu_wave`.
2. Cada job = payload vramd (`*/ums_payload.py` + `resolve_*_vram_opts` hw_auto).
3. **Não** faz preload sync de text3d/paint (`preload=False`) — o 1.º job da
   wave carrega o modelo (evita Broken pipe / free stuck).
4. Master pipeline (topology → … → lod) corre em **CPU** e é **adiado**
   (`MasterDeferQueue`) até a wave GPU relevante acabar — evita misturar
   finalize com thrash text3d↔paint3d.
5. Se vramd indisponível / `queue_full` sem progresso → `FALLBACK_SUBPROCESS`
   (CLI filho; ainda tipicamente com vramd via `try_vramd_delegation`).

```
shapes (text3d) → ensure_to_paint (CPU)
→ paints (paint3d) → drain MasterDeferQueue (master DAG)
→ waves opcionais 2D/audio/terrain conforme manifest
→ simplify / catch-up / handoff
```

---

## Waves

| Wave | Backend | Entry | Notas |
|------|---------|-------|--------|
| Shape | `text3d` | `run_shape_wave_or_fallback` | Omni via `resolve_row_omni` + softfill |
| Paint | `paint3d` | `run_paint_wave_or_fallback` | Peak: mem-eff / SDNQ no payload |
| Text2D | `text2d` | `run_text2d_wave_or_fallback` | |
| Icon | `text2d` | `run_text2d_wave_or_fallback` (`category=icon`, `transparent`) | Modo ícone do Text2D |
| Texture | `texture2d` | `run_texture2d_wave_or_fallback` | |
| Skymap | `skymap2d` | `run_skymap2d_wave_or_fallback` | |
| Sound | `text2sound` | `run_text2sound_wave_or_fallback` | |
| Terrain | `terrain3d` | `run_terrain3d_wave_or_fallback` | |

Specs: `shape_specs_from_items`, `paint_specs_from_items`, …

### Sliding window

`run_gpu_wave` mantém no máximo **~16** jobs submitted em voo
(`window = min(16, max_queue_depth - 1)`). Em `queue_full`, drena waits antes
de mais submits — não explode a fila vramd.

### Peak / quant (obrigatório no payload)

`resolve_text3d_vram_opts` / `resolve_paint3d_vram_opts` / …:

1. Flags explícitas profile/manifest  
2. Senão hw_auto da tool  
3. Fallback admit-safe (~6 GB): tip. `sdnq-int4` + `memory_efficient`

Sem quant no payload → vramd assume fp16 → refuse em 6 GB. **Não** é flag CLI
pública — vai no dict vramd (`with_vramd_peak_opts`).

---

## MasterDeferQueue

| Tipo | Papel |
|------|--------|
| `MasterPendingItem` | `rec` + `mesh_final` + `row` |
| `MasterDeferQueue.enqueue` | Guarda finalize durante a wave |
| `.drain(finalize_fn)` | Corre `run_master_pipeline` (ou equivalente) no fim |

**Anti-padrão:** chamar master (paint→rig→lod) a meio da wave paint — thrash
VRAM e resume inconsistente.

---

## Omni no batch

1. `resolve_row_omni` (pipeline) — merge profile + row.  
2. `softfill_omni_from_category` — se sem controlo geométrico activo.  
3. `prepare_shape_for_generation` / `shape_omni_stale` — sidecar
   `*_shape.omni.json`; fingerprint muda → regen.  
4. `omni_to_cli_flags` / campos no `build_generate_request` (Text3D
   `ums_payload`).

Detalhe: [`OMNI_SHAPE_FINDINGS.md`](OMNI_SHAPE_FINDINGS.md) § soft-fill.

---

## Env / flags

Propagados a filhos GPU (`UMS_CHILD_ENV_KEYS` + `apply_ums_child_env`):

| Var / flag | Papel |
|------------|--------|
| `VRAMD_PRIORITY=batch` | Batch cede a interactive |
| `--vramd-stream` / `VRAMD_STREAM=1` | NDJSON progresso |
| `--no-vramd` | Bypass supervisor (avançado) |
| `VRAMD_DEBUG` | Dump debug nas CLIs |
| `VRAMD_MAX_*` | depth / inflight / affinity |

Após editar código de uma tool: `vramd respawn <backend>` (não precisa restart
do supervisor). Ver [`Vramd/README.md`](../Vramd/README.md).

---

## Várias GPUs e peers

Com mais de uma GPU (local ou peers do vramd — `capacity > 1` no
`{"cmd": "topology"}`) o batch deixa de decidir onde e com que qualidade cada
job corre: dá ao vramd a fila e a informação, e o router decide.

| O quê | Como |
|-------|------|
| **Fila cheia** | Janela rolante (`wave_window`): 16 jobs, ou 4 por slot com muitas GPUs, até `max_depth - 1`. Um wait abre logo espaço para o submit seguinte — o drain por blocos deixava GPUs paradas na cauda. |
| **Paint sem esperar a wave** | `PaintPrefetcher`: quando o shape de um asset acaba, a preparação (topology-fix, refine Hi3D, `_to_paint`) corre num pool em background e o paint entra na fila. Com uma GPU livre, o vramd decide se o pinta já (réplica de paint3d) ou se mantém a GPU no text3d. A wave de paint só espera pelos jobs que encontra pré-submetidos. Desligado com uma GPU (trocaria de modelo a cada job). `VRAMD_PREFETCH_WORKERS` (default 2). |
| **Qualidade pela GPU de destino** | Cada payload leva `hw_tiers`: o hw-auto da tool (`profile_from_specs`) corrido para cada tamanho de GPU disponível. O vramd aplica o da GPU onde o job cai: text3d int4 até ~10 GB e fp16 acima; text2d int3/int4/fp8 conforme a placa. Quant fixado à mão (profile/manifesto) → sem tiers. |
| **Críticos na GPU grande** | `criticality` do asset (manifesto ou categoria: humanoid/creature/weapon/armor/vehicle → `high`; rock/tree/vegetation/prop/food → `low`). O vramd converte a diferença de tier em segundos: um herói espera pela GPU de 24 GB, uma pedra fica na de 6 GB. Os críticos são submetidos primeiro. |
| **Sem `release` entre fases** | `_evict_backend` (text2d→shape, shape→paint) só corre com uma GPU: com várias, cada GPU abre espaço sozinha e um release global despejaria réplicas quentes. |

`vramd route <backend> -p criticality=hero …` mostra a decisão com tier e custo
de qualidade; `vramd devices` / `vramd peers` o que o router vê. Com um vramd
antigo (sem `topology`) tudo volta ao comportamento de uma GPU.

---

## Checklist agente

1. `vramd status` / `queue` / `doctor` — HOLDING, free vs peak.  
2. Batch a correr → **esperar** ou `vramd cancel` / `flush`; nunca pkill GPU.  
3. Shape fail todos com “livre < peak” e vramd idle → `vramd stop` + `vramd start`
   (contexto CUDA residual).  
4. Confirmar payloads com quant (`vramd debug` / logs).  
5. Resume: intermediários em `_intermediate/`; não regenerar shapes só porque
   o path público está vazio.

---

## Testes

- `GameAssets/tests/test_ums_batch.py`, `test_ums_coord.py`
- `test_omni_softfill.py`
- Armadilhas: [`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md)
  § testes com vramd vivo

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Guia operador: waves, window≤16, defer master, softfill, peak hw_auto |
| 2026-11-11 | Várias GPUs/peers: janela rolante por capacidade, prefetch shape→paint, `hw_tiers`, `criticality` |
