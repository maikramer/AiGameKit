# UMS / VRAM — descobertas operacionais

Hub: [`../MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).  
Canónico: [`ModelServer/README.md`](../../ModelServer/README.md),
`Shared/src/aigamekit_shared/model_server.py`,
`GameAssets/src/gameassets/ums_batch.py` / `ums_coord.py`.

---

## Modelo mental

- **Um processo**, um socket (`~/.cache/aigamekit/model-server.sock`), backends GPU
  com carga/evict, fila prioridade + afinidade VRAM, `MAX_INFLIGHT` (tip. 1).
- CLIs chamam `try_ums_delegation` **antes** de prep GPU in-process.
- Alias: `ums` ≡ `aigamekit-model-server`.
- **Autoridade VRAM pública:** UMS + **hw-auto** (por tool). Não expor
  `--low-vram` / `--memory-efficient` ao operador — esses sinais vão no
  **payload** (`with_ums_peak_opts` / `ums_batch.resolve_*_vram_opts`).
- `prepare_gpu_exclusive` / kill / `ensure_vram` agressivo: **só** após UMS
  falhar ou `--no-ums`. Legacy per-tool: `AIGAMEKIT_ALLOW_LEGACY_SERVER=1`.

---

## Pico de admit (o que falhou na prática)

```
peak ≈ weights(quant) + inference_activation + AIGAMEKIT_UMS_VRAM_SAFETY_MIB (384)
```

| Situação | Comportamento correcto |
|----------|------------------------|
| peak > VRAM total GPU | **Hard refuse** — tip `sdnq-int4` / quality fast / mem-eff |
| peak ≤ total mas free < peak | **Transitória**: evict + backoff + **requeue** até `AIGAMEKIT_UMS_MAX_VRAM_RETRIES` (8); poll admit `AIGAMEKIT_UMS_VRAM_ADMIT_WAIT_SEC` |
| Cliente omite quant | UMS assume **fp16** → text3d/paint “não cabem” em 6 GB mesmo com SDNQ real |

**Descoberta batch Omni:** paint/text3d **devem** ter `sdnq_preset` e/ou
`memory_efficient=true` no **payload** (hw-auto / `resolve_*_vram_opts`).
Sem isso, fila morre com “livre < peak” enganoso. Não é flag CLI pública.

---

## Waves UMS (GameAssets)

CLIs GPU → UMS via `try_ums_delegation`. No batch, `ums_batch.py` agrupa em
waves (`run_gpu_wave`, `preload=False`). Guia operador:
[`../GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md).

| Wave | Backends | Entry |
|------|----------|--------|
| Shape | `text3d` | `run_shape_wave_or_fallback` |
| Paint | `paint3d` | `run_paint_wave_or_fallback` |
| Opcionais | `text2d`, `text2icon`, `texture2d`, `skymap2d`, `text2sound`, `terrain3d` | `_run_optional_wave` / `run_*_wave_or_fallback` |

`FALLBACK_SUBPROCESS` → batch usa CLI filho (ainda tipicamente com UMS).
Sliding window: até **16** jobs submitted; em `queue_full` drena waits.
Master CPU: `MasterDeferQueue` até fim da wave (não misturar com GPU thrash).

| Anti-padrão | Efeito visto | Fix |
|-------------|--------------|-----|
| Preload sync text3d >~600 s antes da wave | Client Broken pipe → UMS evict modelo → free stuck ~4 GB < peak → **todos** os jobs falham | **Remover** preload sync; 1º job da wave carrega o shape |
| Preload timeout curto | Mesmo padrão | Se preload existir: default timeout alto (ex. 1800 s) |
| Kill GPU com fila busy | Mata job errado / corrompe fila | `ums cancel` / wait; nunca pkill via NVML/`nvidia-smi` |
| UMS idle com VRAM presa (workers idle ~0.3–1 GiB/contexto) | `livre` (ex. 4378) < peak text3d int4 (~4991) → refuse imediato | Sem jobs: **`ums zero`** (mata workers, supervisor intacto); medir com NVML |

---

## Medir VRAM (NVML)

Fonte canónica no monorepo: `aigamekit_shared.gpu` + dep `nvidia-ml-py`.

| API | Uso |
|-----|-----|
| `query_gpu_free_mib` / `list_gpu_snapshots` | Livre/total/used (+ `source='nvml'\|'smi'`) |
| `list_nvidia_compute_apps` | PIDs compute + MiB |
| `ums doctor` / `ums status` | Peak por backend + free actual |

Fallback subprocess `nvidia-smi` só dentro de `gpu.py` se NVML falhar.

**Não usar** [hf-vram-calc](https://pypi.org/project/hf-vram-calc/) para UMS —
estima LLM/KV-cache; admit usa `FOOTPRINTS` + `vram_planner` + safety.

### Pico calibrado (RTX 4050 6 GB, 2026-07)

| Backend / quant | Peak típ. admit | Nota |
|-----------------|----------------:|------|
| text3d `sdnq-int4` + mem-eff | ~4991 MiB | pesos≈3276 + act≈1331 + safety |
| text3d sem quant no payload | ~8–12 GiB | assume fp16 → hard refuse em 6 GB |

---

## Env knobs úteis

| Var | Papel |
|-----|--------|
| `AIGAMEKIT_UMS_AUTO_START` | Auto-start supervisor (0 = off) |
| `AIGAMEKIT_UMS_PRIORITY` | `batch` nos subprocessos GameAssets |
| `AIGAMEKIT_UMS_STREAM` | `1` = CLIs imprimem NDJSON (equiv. `--ums-stream`) |
| `AIGAMEKIT_UMS_DEBUG` | Dump `ums_debug` nas CLIs |
| `AIGAMEKIT_UMS_MAX_VRAM_RETRIES` | Requeues VRAM transitória (default 8) |
| `AIGAMEKIT_UMS_VRAM_ADMIT_WAIT_SEC` | Poll free antes de refuse |
| `AIGAMEKIT_UMS_VRAM_SAFETY_MIB` | Folga no peak |
| `AIGAMEKIT_UMS_MAX_AFFINITY_CUTS` | Saltos de afinidade (≤3 tip.) |
| `AIGAMEKIT_UMS_MAX_QUEUE_DEPTH` | Cap fila |
| `AIGAMEKIT_UMS_MAX_INFLIGHT` | Paralelismo VRAM |
| `AIGAMEKIT_MODEL_SERVER_SOCKET` | Path socket |
| `AIGAMEKIT_ALLOW_LEGACY_SERVER` | Opt-in servers per-tool + `ensure_vram` legacy |
| `AIGAMEKIT_UMS_SUBPROCESS` | `0` = workers in-process (rollback); default subprocess |
| `AIGAMEKIT_UMS_DEAD_VRAM_MIB` | Residual CUDA “morto” com `loaded=[]` (default 256) |
| ~~`AIGAMEKIT_UMS_DEAD_VRAM_EXIT_SEC`~~ | Documentado mas **nunca implementado** — obsoleto: usar `ums zero` (supervisor já não cria contexto CUDA) |

Runtime pós-load: `aigamekit_shared.vram_budget` — Text3D `num_chunks`, Paint
views/tiles. Desligar Paint auto: `PAINT3D_AUTO_VRAM_BUDGET=0`.

---

## Respawn workers + dead VRAM

Supervisor **não** importa código das tools — backends = subprocessos no
`<Tool>/.venv`. Depois de editar `Text3D/` / `Paint3D` / …:

```bash
ums respawn text3d          # lazy: mata worker; reload no próximo job
ums respawn text3d --hot    # mata + reload imediato
ums respawn                 # todos
```

`RESPAWN_BUSY` se fila/inflight. Restart do processo UMS só para mudanças em
`ModelServer/` ou protocolo partilhado (`worker_protocol`, `worker_serve`).

Com `loaded=[]`, a VRAM residual vive nos **workers idle vivos** (~0.3–1 GiB
de contexto CUDA cada; morrem por idle em 300 s). `ums zero` termina-os já,
**sem parar o supervisor** (`ZERO_BUSY` com fila ocupada). O supervisor em si
não cria contexto CUDA em modo subprocesso: `clear_cuda_memory()` /
`torch_reserved_mib()` saltam as chamadas torch quando
`torch.cuda.is_initialized()` é False — antes desta correção,
`torch.cuda.synchronize()` fazia `_lazy_init()` e o PID UMS ficava com
~1.3 GiB permanentes (só `ums stop` libertava). Status expõe
`process_vram_mib` / `dead_vram_suspect`.

---

## MultiGPU + payload UMS

| Anti-padrão | Efeito | Fix |
|-------------|--------|-----|
| CLI com `--gpu-ids` mas payload UMS sem `gpu_ids` | Load in-process multi-GPU; via UMS single-GPU | `with_ums_load_opts(..., gpu_ids=…)` antes de `try_ums_delegation` |
| Omitir quant no payload pesado | Admit assume fp16 → refuse em 6 GB | hw-auto / `with_ums_peak_opts` → `sdnq_preset` / `memory_efficient` no payload |

O UMS **não** corre `MultiGPUPlanner` no supervisor — só passa `gpu_ids` a
`adapter.load`. Redistribuição de pesos fica na tool.

### Builders canónicos (`*/ums_payload.py`)

Cada tool GPU tem builder de request partilhado CLI ↔ GameAssets wave:

| Backend | Módulo |
|---------|--------|
| text3d | `Text3D/src/text3d/ums_payload.py` |
| paint3d | `Paint3D/src/paint3d/ums_payload.py` |
| text2d / text2icon / texture2d / skymap2d / text2sound / terrain3d | `<pkg>/src/<pkg>/ums_payload.py` |

Usar `with_ums_peak_opts` / `with_ums_load_opts` (`aigamekit_shared.cli_helpers`) —
não montar dicts peak à mão.

### Peak VRAM no batch: hw_auto → admit-safe

`ums_batch.resolve_*_vram_opts` (text3d/paint/text2d/…):

1. Flags explícitas do profile/manifest  
2. Senão `*_HW_AUTO` + `detect_hardware_profile()` da tool  
3. Fallback admit-safe (~6 GB): tip. `sdnq-int4` / `memory_efficient=true`

**Não** hardcodar `sdnq-uint8` na wave — engana admit em GPUs pequenas.

---

## Persistência de fila (WAL)

- Ficheiro: `~/.cache/aigamekit/ums-jobs.jsonl` (`P.WAL_FILENAME`).
- Restart: jobs `queued` rejogam; `running` sem `finished` → requeue.
- **Não** reconstitui modelos em VRAM — só o inventário de pedidos.

---

## Checklist agente (VRAM “ocupada”)

1. `ums status` / `ums queue` / `ums doctor` — ler `HOLDING`, peak/free, legacy sockets.
2. Esperar (`ums wait`, `--ums-stream`) ou `ums cancel` / `flush` se stale.
3. **Não** kill GPU enquanto houver jobs.
4. Confirmar payload tem quant + `gpu_ids` correctos para o backend.
5. Livre < peak com UMS idle (workers vivos / residual): `ums zero` — zera sem parar o supervisor.
6. Só `--no-ums` se bypass intencional; kill continua recusado se UMS busy.
7. Legacy per-tool / `ensure_vram` cego: só com `AIGAMEKIT_ALLOW_LEGACY_SERVER=1`.

---

## Testes com UMS vivo (armadilhas)

| Armadilha | Efeito | Fix nos testes |
|-----------|--------|----------------|
| `patch("aigamekit_shared.gpu.os.kill")` | `gpu.os` **é** o módulo stdlib `os` → captura **todo** `os.kill`, incl. `discover_server_pids` / `is_ums_running` (`kill(pid, 0)`) | Mock também `is_ums_running=False` + `discover_server_pids=set()` (ver `Text3D/tests/test_gpu_kill_aggressive.py`) |
| CLI Click com UMS a correr | Teste “unitário” enfileira job real → VRAM refuse / SystemExit | `--no-ums` **e** mock `prepare_gpu_exclusive` / `warn_if_vram_occupied` (ex. Skymap `test_hw_auto_does_not_clamp_explicit_resolution`) |
| `make test-shared` sem `Shared/.venv` | Cai em `python3` do PATH (ex. 3.14 sem torch) → dezenas de fails falsos | Correr com venv que tem `aigamekit-shared[gpu]` (ex. `GameAssets/.venv/bin/python -m pytest` em `Shared/`) |
| ModelServer sem `.venv` | Idem — preferir venv canónico `ModelServer/.venv` ou sibling com deps | Ver `ModelServer/README.md` (venv supervisor) |

---

## Changelog

| Data | Nota |
|------|------|
| 2026-07-24 | Autoridade UMS+hw-auto; sem CLI `--low-vram`; tabela waves opcionais |
| 2026-07-24 | Guia [`GAMEASSETS_UMS_BATCH.md`](../GAMEASSETS_UMS_BATCH.md); window≤16; `ums respawn`; dead VRAM / IdleEvictor env |
| 2026-07-24 | NVML-first (`nvidia-ml-py`); tip contexto CUDA idle; hf-vram-calc fora de escopo; pico text3d int4 ~4991 MiB |
| 2026-07-24 | `ums_payload` por tool; hw_auto peak no `ums_batch`; armadilhas teste (os.kill / --no-ums / Shared venv) |
| 2026-07-16 | `gpu_ids` CLI→UMS; progress/abort fases 3D+audio+terrain; doctor v2; WAL; legacy `ensure_vram` opt-in; GameAssets `--ums-stream` + dashboard depth/eta |
| 2026-07-19 | VRAM retry/requeue; remoção preload sync shape wave; hard refuse peak>total |
