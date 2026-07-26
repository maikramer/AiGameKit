# Plano — ModelServer subprocess-per-backend

Data: 2026-07-21 · **Estado (2026-07-24): Fases 0–4 implementadas em produção.**

Este ficheiro permanece como desenho + protocolo. Operação viva:
[`ModelServer/README.md`](../ModelServer/README.md) (`ums respawn`, workers,
`GAMEDEV_UMS_SUBPROCESS=0` rollback). Batch: [`GAMEASSETS_UMS_BATCH.md`](GAMEASSETS_UMS_BATCH.md).

## Contexto

O ModelServer (UMS) arrancava no venv da primeira tool que o chamava (ex.: `Text3D/.venv`),
herdava só os pacotes dessa tool, e falhava com `ImportError` quando outro backend
era pedido (ex.: `paint3d`). O cliente caía em fallback in-process e abria um
subprocesso que competia pela GPU — derrotando o propósito do supervisor único.

## Solução acordada

UMS num venv canónico leve (`ModelServer/.venv`); cada backend corre num
**subprocesso worker persistente** no venv da própria tool, falando JSONL via
stdin/stdout. O UMS coordena VRAM via NVML (soma todos os PIDs filhos) + reports
do worker.

## Decisões

| Decisão | Escolha |
|---|---|
| Lifecycle worker | Persistente por backend (carrega no arranque, vivo entre jobs; evict=unload; idle_timeout=shutdown) |
| Piloto | Todos os 9 backends GPU |
| Protocolo | stdin/stdout JSONL (UMS escreve comandos no stdin; worker emite eventos NDJSON no stdout; logs via stderr) |
| Venv canónico | `./install.sh modelserver` cria `ModelServer/.venv`; auto-start dá precedência máxima a esse venv |

## Protocolo JSONL

**UMS → Worker (stdin):**
- `{"cmd":"load","kwargs":{...}}`
- `{"cmd":"generate","request":{...}}`
- `{"cmd":"unload"}`
- `{"cmd":"abort"}`
- `{"cmd":"shutdown"}`

**Worker → UMS (stdout):**
- `{"event":"ready","vram_mib":1300}`
- `{"event":"progress","pct":0.25,"msg":"..."}`
- `{"event":"vram_budget",...}`
- `{"event":"done","result":{...}}`
- `{"event":"unloaded"}`
- `{"event":"error","error":"...","error_code":"..."}`

stderr do worker → `~/.cache/gamedev/ums-worker-<backend>.log`.
Abort cooperativo via `{"cmd":"abort"}`; SIGTERM como fallback de força-bruta.

## Fases

### Fase 0 — Venv canónico e auto-start correto ✅

- `Shared/src/gamedev_shared/model_server.py`: nova `_resolve_ums_start_cmd()` com
  precedência correcta (MODELSERVER_BIN → ModelServer/.venv → PATH → sys.executable
  com warning). `ensure_ums_running` usa-a.
- Testes: `TestResolveUmsStartCmd` (6 casos em `Shared/tests/test_model_server.py`).
- Docs: `ModelServer/README.md` (secção Instalação) + `AGENTS.md` (canonical venv).

### Fase 1 — Infraestrutura subprocess ✅

- `ModelServer/src/modelserver/subprocess_pool.py` — `SubprocessWorkerPool` (spawn
  persistente, JSONL via stdin/stdout, watchdog/abort SIGTERM). Inspirado em
  `Shared/src/gamedev_shared/subprocess_utils.py:119`.
- `Shared/src/gamedev_shared/worker_protocol.py` — contrato JSONL partilhado
  (esquemas `cmd`/`event`, helpers `send_cmd`/`read_event`/`emit_event`).
- `ModelServer/src/modelserver/data/backends.yaml` — cada backend ganha `tool: <name>`.
- `ModelServer/src/modelserver/registry.py` — `BackendDescriptor.tool: str | None`.

### Fase 2 — `serve --ums-worker` em cada tool ✅

- `Shared/src/gamedev_shared/worker_serve.py` — `run_worker_loop(adapter_class, backend_name)`.
- Por tool: `<Tool>/src/<tool>/worker_serve_adapter.py` + subcomando `serve` no CLI.
- Adapter local da tool espelha o adapter do UMS (mesmas lambdas), mas corre no
  venv da tool.

### Fase 3 — BackendManager híbrido ✅

- `_LoadedState` ganha `subprocess: SubprocessWorker | None`.
- `ensure_loaded` / `_evict_unlocked` / `generate` despacham para subprocesso
  quando `desc.tool` está definido.
- `_admit_free_mib` soma VRAM de todos os PIDs filho via NVML.
- Feature flag `GAMEDEV_UMS_SUBPROCESS=0` volta ao comportamento in-process.

### Fase 4 — Migração dos 9 backends + rollback ✅

**Pós-fase (ops):** `ums respawn [backend] [--hot]` recarrega código do worker
sem reiniciar o supervisor; `RESPAWN_BUSY` se fila/inflight. Dead VRAM residual:
`GAMEDEV_UMS_DEAD_VRAM_MIB` / `GAMEDEV_UMS_DEAD_VRAM_EXIT_SEC` + IdleEvictor
(ver `findings/UMS_VRAM_FINDINGS.md`).

- Adicionar `tool:` em cada entrada do `backends.yaml`.
- Rollback automático: subprocesso falha 2× seguidas → `error_code=BACKEND_VENV_MISSING`
  (sem fallback silencioso in-process).

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Latência IPC JSONL | Negligenciável vs. inferência (segundos-minutos) |
| Worker morre mid-job | Watchdog no pool: re-spawn + requeue com `WORKER_DIED` |
| Venv em falta | `BACKEND_VENV_MISSING` explícito; `doctor` reporta |
| Regressão dispersa (9 backends) | Feature flag `GAMEDEV_UMS_SUBPROCESS=0`; faseada um commit por backend |

## VRAM accounting no modelo subprocesso

1. Worker reporta `vram_mib` em `ready` e em `progress` (via `process_vram_mib()` NVML).
2. UMS soma PIDs filhos + próprio via `list_nvidia_compute_apps()` filtrado por PPID.
3. Residual entre jobs: deixa de ser problema — modelo está carregado de propósito;
   evict descarrega mesmo (worker passa de `ready` para `idle unloaded`).
