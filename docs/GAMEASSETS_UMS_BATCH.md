# GameAssets — UMS batch waves (operador)

Happy path do `gameassets batch` quando a GPU passa pelo **Unified Model Server**.
Complementa [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md) §8 e
[`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md).

**Código:** `GameAssets/src/gameassets/ums_coord.py`, `ums_batch.py`,
`batch_cmd.py` · **Omni soft-fill:** `omni_ctrl.py` · **DAG pós-GPU:**
[`findings/MESH_PIPELINE_FINDINGS.md`](findings/MESH_PIPELINE_FINDINGS.md) (Round 3).

---

## Modelo mental

1. Batch agrupa jobs GPU em **waves** (mesmo backend) → `run_gpu_wave`.
2. Cada job = payload UMS (`*/ums_payload.py` + `resolve_*_vram_opts` hw_auto).
3. **Não** faz preload sync de text3d/paint (`preload=False`) — o 1.º job da
   wave carrega o modelo (evita Broken pipe / free stuck).
4. Master pipeline (topology → … → lod) corre em **CPU** e é **adiado**
   (`MasterDeferQueue`) até a wave GPU relevante acabar — evita misturar
   finalize com thrash text3d↔paint3d.
5. Se UMS indisponível / `queue_full` sem progresso → `FALLBACK_SUBPROCESS`
   (CLI filho; ainda tipicamente com UMS via `try_ums_delegation`).

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
| Icon | `text2icon` | `run_text2icon_wave_or_fallback` | |
| Texture | `texture2d` | `run_texture2d_wave_or_fallback` | |
| Skymap | `skymap2d` | `run_skymap2d_wave_or_fallback` | |
| Sound | `text2sound` | `run_text2sound_wave_or_fallback` | |
| Terrain | `terrain3d` | `run_terrain3d_wave_or_fallback` | |
| Motion | `motion3d` | `run_motion3d_wave_or_fallback` | NPZ / HML22 source GLB; **não** skinned |

Specs: `shape_specs_from_items`, `paint_specs_from_items`, `motion3d_specs_from_items`, …

**Motion3D skinned bake** não é wave UMS: após o NPZ, o operador (ou um passo
CPU do batch) corre `motion3d apply-rigged <npz> <rigged.glb> -o <out.glb>`
(Animator3D `hml22`). Ver [`findings/MOTION3D_FINDINGS.md`](findings/MOTION3D_FINDINGS.md).

### Sliding window

`run_gpu_wave` mantém no máximo **~16** jobs submitted em voo
(`window = min(16, max_queue_depth - 1)`). Em `queue_full`, drena waits antes
de mais submits — não explode a fila UMS.

### Peak / quant (obrigatório no payload)

`resolve_text3d_vram_opts` / `resolve_paint3d_vram_opts` / …:

1. Flags explícitas profile/manifest  
2. Senão hw_auto da tool  
3. Fallback admit-safe (~6 GB): tip. `sdnq-int4` + `memory_efficient`

Sem quant no payload → UMS assume fp16 → refuse em 6 GB. **Não** é flag CLI
pública — vai no dict UMS (`with_ums_peak_opts`).

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
| `AIGAMEKIT_UMS_PRIORITY=batch` | Batch cede a interactive |
| `--ums-stream` / `AIGAMEKIT_UMS_STREAM=1` | NDJSON progresso |
| `--no-ums` | Bypass supervisor (avançado) |
| `AIGAMEKIT_UMS_DEBUG` | Dump debug nas CLIs |
| `AIGAMEKIT_UMS_MAX_*` | depth / inflight / affinity |

Após editar código de uma tool: `ums respawn <backend>` (não precisa restart
do supervisor). Ver [`ModelServer/README.md`](../ModelServer/README.md).

---

## Checklist agente

1. `ums status` / `queue` / `doctor` — HOLDING, free vs peak.  
2. Batch a correr → **esperar** ou `ums cancel` / `flush`; nunca pkill GPU.  
3. Shape fail todos com “livre < peak” e UMS idle → `ums stop` + `ums start`
   (contexto CUDA residual).  
4. Confirmar payloads com quant (`ums debug` / logs).  
5. Resume: intermediários em `_intermediate/`; não regenerar shapes só porque
   o path público está vazio.

---

## Testes

- `GameAssets/tests/test_ums_batch.py`, `test_ums_coord.py`
- `test_omni_softfill.py`
- Armadilhas: [`findings/UMS_VRAM_FINDINGS.md`](findings/UMS_VRAM_FINDINGS.md)
  § testes com UMS vivo

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Guia operador: waves, window≤16, defer master, softfill, peak hw_auto |
