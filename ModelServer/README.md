# Unified Model Server (UMS)

Supervisor único de VRAM para o monorepo GameDev. Um processo detém toda a VRAM
da máquina e roteia pedidos de geração para **backends** (ferramentas GPU)
via **fila inteligente** (prioridade + afinidade VRAM) e evicção **peso + LRU**.

## Problema que resolve

Antes do UMS, cada ferramenta GPU (Text2Icon, Texture2D, ...) corria o seu próprio
model server num socket separado, peer-to-peer. Problemas:

- **Sem "cérebro"**: Nenhuma entidade sabe o inventário completo (o que está
  carregado, quanto pesa, quando foi usado).
- **Evicção cega**: `ensure_vram_available` pedia `release` a todos os servers
  sem saber qual liberta quanta VRAM.
- **Consumers pesados desprotegidos**: Text3D/Paint3D não eram servers — os pesos
  Hunyuan deles ficavam sujeitos a SIGTERM de irmãos.
- **Sem fila**: pedidos bloqueavam em locks opacos, sem backpressure nem prioridade.

O UMS resolve isto com **1 socket, 1 processo, inventário global + scheduler**.

**Descobertas ops (admit vs runtime, batch waves, anti-patterns):**  
[`docs/MODEL_FINDINGS.md`](../docs/MODEL_FINDINGS.md) ·
[`docs/findings/UMS_VRAM_FINDINGS.md`](../docs/findings/UMS_VRAM_FINDINGS.md).

## Arquitetura

```
┌──────────────────────────────────────────────────────────────┐
│            Unified Model Server (1 processo)                  │
│  ~/.cache/gamedev/model-server.sock                           │
│                                                               │
│  JobQueue → AffinityScheduler → WorkerPool (max_inflight=1)   │
│                         │                                     │
│                         ▼                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                    │
│  │BackendMgr│  │VRAMPlan  │  │ Registry │                    │
│  │load/evict│  │peso+LRU  │  │9 adapters│                    │
│  └──────────┘  └──────────┘  └──────────┘                    │
└──────────────────────────────────────────────────────────────┘
       ▲          ▲          ▲          ▲
   text2icon   texture2d   text3d    paint3d   (clientes)
```

### Fila inteligente

1. **Prioridade**: `interactive` (CLI) > `batch` (GameAssets exporta `GAMEDEV_UMS_PRIORITY=batch`).
2. **Afinidade VRAM**: se a cabeça da fila precisa de um backend *frio* e existe
   mais atrás um job cujo backend já está em VRAM, o scheduler salta a cabeça
   (até **3 cuts** por job-cabeça). No 4.º, força atender o aguardando (unload se preciso).
3. **Backpressure**: `MAX_QUEUE_DEPTH` (default 32) → resposta `queue_full`.
4. **GPU**: `MAX_INFLIGHT=1` — uma geração de cada vez na GPU.

Env overrides: `GAMEDEV_UMS_MAX_AFFINITY_CUTS`, `GAMEDEV_UMS_MAX_QUEUE_DEPTH`,
`GAMEDEV_UMS_MAX_INFLIGHT`, `GAMEDEV_UMS_STARVATION_TIMEOUT_SEC` (0=off),
`GAMEDEV_UMS_PRIORITY`, `GAMEDEV_UMS_STREAM=1` (mesmo efeito que `--ums-stream` nas CLIs),
`GAMEDEV_UMS_DEBUG=1`, `GAMEDEV_UMS_AUTO_START_LOG`,
`GAMEDEV_ALLOW_LEGACY_SERVER=1` (servers per-tool + fallback legacy de `ensure_vram`),
`GAMEDEV_LOG_DIR` / `GAMEDEV_LOG_FILE` / `GAMEDEV_FILE_LOG` (ficheiros UMS —
[`docs/LOGGING.md`](../docs/LOGGING.md)).

**Cancel / progresso cooperativo** (não mata CUDA mid-kernel):

| Backend | Progresso | Abort |
|---------|-----------|-------|
| text2icon / text2d / texture2d / skymap2d | steps Diffusers (`callback_on_step_end`) | entre steps |
| text2sound | callback do sampler stable-audio | entre steps |
| text3d | fases: T2D/ref → shape → save | entre fases |
| paint3d | load mesh → paint → save | entre fases |
| part3d | segment / parts → save | entre fases |
| terrain3d | diffusion → export | entre fases |

**ETA / métricas:** `status`/`queue`/`stats` incluem `eta_sec` + `queue_metrics`
(cuts, wait p50/p95, `queue_full_count`, `affinity_hits` no `debug`).
**Inflight:** `MAX_INFLIGHT>1` só arranca jobs em paralelo se VRAM livre couber.
**WAL:** jobs `queued` persistem em `~/.cache/gamedev/ums-jobs.jsonl`; no restart
rejogam-se; jobs `running` no crash → requeue.

### Debug nas respostas

Todas as respostas relevantes trazem campos estáveis para diagnóstico:

| Campo | Onde | Conteúdo |
|-------|------|----------|
| `error_code` | erros / `queue_full` | Código estável (`BACKEND_UNKNOWN`, `QUEUE_FULL`, `GENERATE_FAILED`, `CANCELLED`, `TIMEOUT`, …) |
| `hint` | erros | Acção sugerida (uma linha) |
| `ums_debug` | `generate` / `wait` / `poll` / preload / ensure-vram / `queue_full` | `job_id`, `backend`, `priority`, `state`, `affinity_cuts`, timings (`queue_wait_sec`, `generate_sec`, `total_sec`), `loaded_backends`, … |
| `debug` | `status` / `queue` | Snapshot: loaded, `last_errors`, profundidade da fila |

```bash
gamedev-model-server status --json   # inclui debug.last_errors
gamedev-model-server queue --json
gamedev-model-server debug           # HOLDING + fila + budgets (só leitura)
gamedev-model-server stats           # backends + queue p50/p95 + last_runtime_budget
gamedev-model-server bench           # RTT IPC; não submete GPU
GAMEDEV_UMS_DEBUG=1 text2icon generate "x" -o out.png
```

Descobertas multi-modelo (footprints, runtime budget, kernel opts, batch):  
[`docs/MODEL_FINDINGS.md`](../docs/MODEL_FINDINGS.md).

## Backends suportados (9)

| Backend | Tool | VRAM (MiB) | Evict priority | API normalizada |
|---------|------|------------|----------------|-----------------|
| text2icon | Text2Icon | 3000 | 20 | `warmup()` |
| texture2d | Texture2D | 2500 | 20 | `warmup()` |
| text2d | Text2D | 4500 | 25 | `warmup()` |
| skymap2d | Skymap2D | 7000 | 25 | `warmup()` |
| text3d | Text3D | ~10000 YAML / admit via `FOOTPRINTS` Omni ~10+2 GiB fp16 | 40 | `_load_hunyuan()` |
| paint3d | Paint3D | 4000 | 40 | context-manager |
| part3d | Part3D | 5200 | 35 | pipeline |
| text2sound | Text2Sound | 5000 | 30 | `load()` |
| terrain3d | Terrain3D | 6000 | 40 | procedural |

**VRAM** na tabela = hint YAML; **admit real** usa `gamedev_shared.lowvram.FOOTPRINTS` + quant
(ver hub [`docs/MODEL_FINDINGS.md`](../docs/MODEL_FINDINGS.md)).
**Evict priority** = maior = manter carregado; menor = evicted primeiro.

## Instalação

O ModelServer tem o seu **próprio venv canónico** (`ModelServer/.venv`) que serve
de supervisor único para todas as tools GPU. Sem este venv, o UMS arranca no venv
da primeira tool que o chama e falha ao importar outras tools (ex.: `paint3d`
quando o UMS arranca em `Text3D/.venv`).

```bash
# Recomendado: via clified (cria ModelServer/.venv com gamedev_shared + modelserver)
./install.sh modelserver

# Manual (equivalente)
cd Shared && pip install -e .
cd ../ModelServer && pip install -e .
```

O auto-start do UMS dá **precedência máxima** ao venv canónico: se
`ModelServer/.venv/bin/python` existe, é sempre esse o interpretador usado
(independentemente do venv da tool que chama `delegate_to_ums`). Sem venv
canónico nem `MODELSERVER_BIN`, cai para o venv actual com warning visível no
log (situação incorrecta — instalar via `./install.sh modelserver`).

## Uso

### Logs em ficheiro

`ums start` chama `configure_logging("ums")` — path mostrado no painel de arranque.
Ficheiro diário: `~/.cache/gamedev/logs/ums-YYYY-MM-DD.log`.

- Mensagens internas (`_log`) **sempre** vão para o ficheiro.
- Consola detalhada só com `ums start -v` / `--verbose`.
- Guia: [`docs/LOGGING.md`](../docs/LOGGING.md) · [PT](../docs/LOGGING_PT.md).

```bash
# Alias curto: ums ≡ gamedev-model-server
ums start
ums start -v        # também ecoa [UMS] na consola
ums status          # backends + HOLDING/QUEUE + tip "não mates GPU"
ums status --json   # dump completo
ums queue           # jobs + timings/progress + tip
ums queue --json
ums wait <job_id>   # bloqueia até o job terminar (aceita prefixo)
ums backends
ums preload text2icon
ums cancel <job_id> # UUID ou prefixo único
ums cancel --all    # limpa fila (+ pede cancel aos running)
ums flush           # alias de cancel --all
ums flush --queued-only
ums evict text2icon
ums respawn text3d        # reinicia SÓ o worker da tool (código novo, sem tocar no supervisor)
ums respawn               # todos os backends com worker subprocesso
ums respawn text3d --hot  # mata e recarrega já o modelo (fica quente)
ums stats
ums doctor
ums stop
```

### Reiniciar workers sem reiniciar o UMS (`ums respawn`)

Cada backend com `tool:` no `backends.yaml` corre num **subprocesso persistente**
no venv da tool (ex.: `Text3D/.venv/bin/python -m text3d serve --ums-worker`).
O supervisor UMS **não importa código de nenhuma tool** — mudar código de
`Text3D/`, `Paint3D/`, etc. **não** obriga a reiniciar o UMS.

O catch: o worker persistente mantém os módulos da tool em memória entre jobs.
`ums evict` só descarrega os **pesos** do modelo; não recarrega o código. Depois
de editares código da tool (ex.: `Text3D/src/text3d/utils/export.py` onde mora o
`save_mesh` do GLB), usa `ums respawn` para matar o subprocesso do worker e
arrancar um novo no venv da tool — o próximo `generate` já corre o código
atualizado.

```bash
# Editei Text3D/src/text3d/utils/export.py — quero que o próximo generate use o código novo:
ums respawn text3d            # lazy (default): mata o worker; reload no próximo generate
ums respawn text3d --hot      # mata e recarrega já o modelo (quente na próxima chamada)
ums respawn                   # todos os backends com worker subprocesso (várias tools editadas)
```

**Guard:** o respawn é recusado (`RESPAWN_BUSY`) se houver jobs na fila ou em
curso — matar um worker a meio de um `generate` rouba o job. Espera com
`ums queue` / `ums wait` ou cancela com `ums flush` primeiro.

**Único restart obrigatório:** mudar código do **próprio UMS**
(`ModelServer/src/modelserver/*.py`), do `backends.yaml`, ou do protocolo
partilhado (`Shared/.../worker_protocol.py`, `worker_serve.py`) — esses vivem no
processo supervisor. Para tudo o resto, `ums respawn` chega.

### Robustez: singleton, reap de órfãos e timers de idle

Três mecanismos garantem que ninguém fica com VRAM presa sem dono
(`src/modelserver/process_guard.py`, `idle_evictor.py`):

**1. Singleton por `flock`.** O supervisor adquire
`~/.cache/gamedev/model-server.lock` **antes** de tocar no socket. O kernel
liberta o lock na morte do processo (incluindo `SIGKILL`), logo nunca há estado
stale — ao contrário do pid-file. Um segundo `ums start` falha com
`RuntimeError: UMS já ativo (PID …)`.

> Regressão histórica que isto fecha: o arranque apagava o socket quando o probe
> `is_server_running` falhava. Num supervisor **vivo mas ocupado** o probe falha,
> e o novo supervisor fazia bind por cima — resultado medido: 3 supervisores
> vivos, um deles com um worker `text3d` a segurar 3.5 GiB invisíveis no
> `ums status` (que só fala com o dono do socket).

**2. Reap de órfãos.** Com o lock nas mãos, qualquer outro processo da família
UMS é lixo de uma run anterior. No arranque (`GAMEDEV_UMS_REAP_ON_START=1`, ou
manualmente via `ums reap` / `ums doctor --fix`) esses processos levam
SIGTERM → SIGKILL. Self, descendentes **e ascendentes** ficam protegidos: um
lançador como `timeout 900 python -m modelserver start` carrega a nossa cmdline
no argv dele. O `ensure_vram` também faz reap como último recurso antes de
recusar um job por VRAM.

Do lado do worker há duas redes contra sobreviver ao supervisor: EOF no stdin e
o watchdog de PPID (`gamedev_shared.worker_serve.start_parent_watchdog`,
desligável com `GAMEDEV_WORKER_PARENT_WATCHDOG=0`). Não se usa
`PR_SET_PDEATHSIG` porque no Linux dispara com a morte da *thread* que criou o
processo — e o spawn acontece nas threads do `WorkerPool`.

**3. Timers de idle (3 níveis).**

| Nível | Default | Env | Efeito |
|-------|---------|-----|--------|
| `unload` dos pesos | 120 s | `GAMEDEV_UMS_IDLE_EVICT_SEC` | Liberta a maior parte da VRAM; worker fica vivo (reload rápido) |
| `shutdown` do worker | 300 s | `GAMEDEV_UMS_WORKER_IDLE_SHUTDOWN_SEC` (0 desliga) | Mata o subprocesso — o `unload` deixa o contexto CUDA (~0.3-1 GiB) preso ao processo |
| self-shutdown do supervisor | 30 min | `GAMEDEV_UMS_IDLE_TIMEOUT_MIN` (0 desliga) | Os clientes fazem auto-start quando precisam |

Intervalo de verificação: `GAMEDEV_UMS_IDLE_EVICT_CHECK_SEC` (15 s). Health-check
`ping`/`pong` aos workers vivos a cada `GAMEDEV_UMS_WORKER_HEALTH_CHECK_SEC`
(60 s): um worker wedged segura VRAM sem terminar jobs — mata-se e o próximo job
faz respawn. Flags equivalentes no arranque: `ums start --idle-evict-sec` /
`--worker-shutdown-sec`.

Os tempos contam desde o **último job**, não desde o unload. Em batch contínuo o
modelo fica quente porque cada job renova o relógio; 120 s evita pagar cold start
(dezenas de segundos em `text3d`/`paint3d`) nos intervalos entre stages.

**Contabilidade honesta de VRAM.** `ums status` mostra `VRAM nos workers`
(soma NVML dos subprocessos) e uma tabela `Processos UMS órfãos` com a VRAM que
seguram — antes só se via o PID do supervisor, que com subprocess-per-backend
está quase sempre vazio.

```bash
ums reap --dry-run   # lista órfãos + VRAM, sem matar
ums reap             # SIGTERM → SIGKILL; delegado no UMS se estiver ativo
ums doctor --fix     # diagnóstico + reap do que é seguro
```

### Agents / anti-patterns

A fila UMS é a **autoridade** de quem usa a GPU. Se NVML / `ums doctor` /
`list_gpu_snapshots()` mostram VRAM ocupada:

1. Corre `ums status` / `ums queue` — lê a linha `HOLDING: … | QUEUE: …`.
2. Espera (`ums wait <job_id>`, ou a tool com `--ums-stream`) **ou** `ums cancel <job_id>` /
   `ums flush` (fila stale). Jobs com `vram_retries` / progress «VRAM insuficiente —
   evict+espera» estão a **aguardar** VRAM transitória — não são falhas finais ainda.
3. **Não** mates PIDs GPU (`kill`, scripts, `--gpu-kill-others`) enquanto houver
   jobs UMS — o kill agressivo **recusa** nesse caso e as tools imprimem o tip
   `UMS_DO_NOT_KILL_TIP`.
4. Tools pesadas (`text3d`, `paint3d`) enfileiram no UMS **antes** de
   `ensure_vram` / kill; GPU prep só no fallback in-process.
5. Se a CLI diz `UMS indisponível — fallback in-process`, arranca
   `ums start` (ou deixa o auto-start) em vez de assumir que a GPU está “livre”.
6. Testes unitários que mockam `os.kill` / CLI Click: ver armadilhas em
   [`docs/findings/UMS_VRAM_FINDINGS.md`](../docs/findings/UMS_VRAM_FINDINGS.md)
   (patch `gpu.os.kill` é global; CLI precisa `--no-ums` + mock GPU prep).
7. VRAM ocupada mas `ums status` diz `loaded=0`? Olha a tabela **Processos UMS
   órfãos** no status e corre `ums reap` — é lixo de uma run anterior, não um
   job legítimo.
8. UMS com **0 backends** mas ainda ~1 GiB+ em CUDA context: free pode ficar
   abaixo do peak (ex. text3d `sdnq-int4` ~4991 MiB). Sem jobs na fila →
   `ums stop` + `ums start` limpa o contexto; **não** pkill com fila busy.
   Free VRAM: `gamedev_shared.gpu.query_gpu_free_mib` (NVML-first).
   Residual “morto” com `loaded=[]`: status `process_vram_mib` /
   `dead_vram_suspect`; IdleEvictor pode self-exit se
   `GAMEDEV_UMS_DEAD_VRAM_MIB` (default 256) persistir ≥
   `GAMEDEV_UMS_DEAD_VRAM_EXIT_SEC` (20 s) com fila vazia.

### Integração com CLIs das tools

As CLIs GPU delegam no UMS (auto-start se `GAMEDEV_UMS_AUTO_START≠0`):
`text2icon`, `texture2d`, `text2d`, `skymap2d`, `text3d`, `paint3d`, `part3d`,
`text2sound`, `terrain3d`.

**Autoridade VRAM:** UMS + **hw-auto** (default). Não há CLI pública
`--low-vram` / `--memory-efficient` — hw-auto preenche `sdnq_preset` /
`memory_efficient` no payload (`with_ums_peak_opts`). `prepare_gpu_exclusive`
só depois de UMS falhar ou `--no-ums`. Servers per-tool:
`GAMEDEV_ALLOW_LEGACY_SERVER=1`.

**GameAssets waves** (`ums_batch.py`): shape (`text3d`) + paint (`paint3d`) +
opcionais `text2d` / `text2icon` / `texture2d` / `skymap2d` / `text2sound` /
`terrain3d`. Guia:
[`docs/GAMEASSETS_UMS_BATCH.md`](../docs/GAMEASSETS_UMS_BATCH.md) ·
[`docs/findings/UMS_VRAM_FINDINGS.md`](../docs/findings/UMS_VRAM_FINDINGS.md).

Flags partilhadas (via `gamedev_shared.cli_helpers.add_ums_options`):

| Flag | Descrição |
|------|-----------|
| `--ums-priority interactive\|batch` | Prioridade na fila (default: interactive / `GAMEDEV_UMS_PRIORITY`) |
| `--no-ums` | Força geração in-process (ignora UMS) |
| `--ums-stream` | Mostra eventos de fila/progresso (NDJSON) |

```bash
gamedev-model-server start
text2icon generate "espada" -o sword.png
texture2d generate "madeira" -o wood.png --ums-stream
text3d generate "goblin" -o goblin.glb --ums-priority interactive --gpu-ids 0,1
# Batch GameAssets: GAMEDEV_UMS_PRIORITY=batch (+ --ums-stream → GAMEDEV_UMS_STREAM=1)
```

**MultiGPU via UMS:** as CLIs injectam `gpu_ids` no payload (`with_ums_load_opts`
em `gamedev_shared.cli_helpers`). O `BackendManager` passa `gpu_ids` (e
`verbose` / `sdnq_preset` / `quant_mode` / `offload`) a `adapter.load`. Sem isto,
`--gpu-ids` só funciona in-process e perde-se na delegação UMS.

### Kernel opts no load (defaults)

Adapters aplicam defaults de `torch.compile` / `channels_last` no `load()`
(calibrados em RTX 4050 6 GB). Override via kwargs de preload / payload:

| Backend | Default no load |
|---------|-----------------|
| `text2d` | `torch_compile=True`, `channels_last=True` |
| `skymap2d` | `torch_compile=True` |
| `text2icon` | `channels_last=True`, `torch_compile=False` |

Preload aceita: `torch_compile`, `torch_compile_mode`, `channels_last`
(`modelserver/server.py`). Guia: [`docs/findings/KERNEL_OPTS_FINDINGS.md`](../docs/findings/KERNEL_OPTS_FINDINGS.md).

`queue_full` **não** faz fallback in-process (evita segundo modelo na GPU) —
a CLI termina com erro; aumenta `GAMEDEV_UMS_MAX_QUEUE_DEPTH` ou espera.

### `ums doctor`

```bash
ums doctor
```

Verifica: UMS up, fila (`depth`/`inflight`/`eta`/`affinity_hits`/`queue_full`),
**peak vs free** por backend carregado, **sockets legacy** activos (conflito),
GPU NVIDIA, `hf_xet`, import deps de cada backend. Se a fila tem jobs, imprime
hint explícito: não mates GPU — usa `queue` / `cancel` / `wait`.

### Coordenação de VRAM

**Pico = pesos(quant) + activação de inferência + `GAMEDEV_UMS_VRAM_SAFETY_MIB`
(default 384).** O UMS **recusa** load/generate se VRAM livre < pico *e* o pico
nunca cabe na GPU (ex.: text3d fp16 full ~8 GiB numa 6 GB) — tip: hw-auto
(`sdnq-int4`) / `--quality fast`. Sinais `memory_efficient` / `sdnq_preset` no
**payload** (não flags CLI públicas) reduzem o pico admitido.

**VRAM transitória** (livre < pico mas pico ≤ VRAM total — processo externo,
fragmentação CUDA): o worker faz `evict_all` + backoff exponencial + **requeue**
até `GAMEDEV_UMS_MAX_VRAM_RETRIES` (default 8). `ensure_loaded` também espera
até `GAMEDEV_UMS_VRAM_ADMIT_WAIT_SEC` (default 8s) a fazer poll. Assim um batch
não morre 60/60 quando a GPU está momentaneamente ocupada. `status`/`queue`
mostram `vram_retries`; `progress` reporta a espera.

Ferramentas pesadas, **no path in-process**, chamam
`ensure_vram_available(N, backend="text3d")`. Com UMS ativo → `ensure-vram` usa
`max(N, peak(backend))`. Sem UMS / UMS sem resposta: release a sockets **legacy**
só com `GAMEDEV_ALLOW_LEGACY_SERVER=1` (default off — evita corridas com o UMS).
Com jobs na fila, `kill_gpu_compute_processes_aggressive` recusa matar
(`respect_ums_queue=True`).

**Runtime budget (pós-load):** o admit acima é estático. Depois dos pesos/offload,
`gamedev_shared.vram_budget` (reexportado em `modelserver.runtime_budget`)
dimensiona o batch de activação pela VRAM **livre** — Text3D `num_chunks`,
Paint3D views/tiles/DINO — para não OOM→CPU. Contrato: model objects expõem
`refresh_runtime_budget(**hints)`; adapters chamam
`BackendAdapter.apply_runtime_budget(model, request, ...)` antes da inferência
(Paint3D aceita overrides por-request `max_num_view`/`view_resolution`,
clamped ao shape do load) e devolvem o dict em `runtime_budget` na resposta —
o BackendManager guarda o último em `ums stats` (`last_runtime_budget`).
Text3D aplica `auto_num_chunks` no momento do decode e reporta o efetivo.
Desligar Paint: `PAINT3D_AUTO_VRAM_BUDGET=0`. Env
`TEXT3D_DECODE_BYTES_PER_QUERY` calibra o custo por query do decode.

Números calibrados (96 KiB/query, ~280 MiB/view, DINO 1.6 GiB, …) e checklist 6 GB:
[`docs/MODEL_FINDINGS.md`](../docs/MODEL_FINDINGS.md) §3–5, §11.

## Protocolo

JSON / NDJSON sobre Unix socket (`~/.cache/gamedev/model-server.sock`):

| Request | Comportamento |
|---------|---------------|
| `{"cmd":"generate","backend":"…",…}` | Enfileira + espera (sync). Opcional: `priority`, `stream` |
| `{"cmd":"submit","backend":"…",…}` | Enfileira; devolve `job_id` |
| `{"cmd":"poll","job_id":"…"}` | Estado do job |
| `{"cmd":"wait","job_id":"…"}` | Bloqueia até terminar |
| `{"cmd":"cancel","job_id":"…"}` | Cancela queued; running best-effort (prefixo OK) |
| `{"cmd":"cancel","all":true}` / `{"cmd":"flush"}` | Cancela todos (`queued_only` opcional) |
| `{"cmd":"queue"}` | Snapshot da fila |
| `{"cmd":"release"}` / `release`+`backend` | Evict |
| `{"cmd":"status"}` / `stats` | Estado + fila |
| `{"cmd":"preload","backend":"X"}` | Pré-aquece |
| `{"cmd":"ensure-vram","needed_mib":N}` | Evicta até N MiB livres |
| `{"cmd":"respawn","backend":"X","lazy":true}` | Reinicia o worker subprocesso (código novo da tool) |
| `{"cmd":"shutdown"}` | Graceful |

Com `stream: true` em `generate`/`wait`: linhas NDJSON
`queued` → `started` → `progress` → resultado final (`status` ok/error).

Cliente Shared: `delegate_to_ums`, `submit_to_ums`, `poll_ums_job`, `wait_ums_job`,
`cancel_ums_job`, `cancel_ums_all`, `respawn_ums_backend`, `send_request_stream`.

## Retrocompatibilidade

- Per-tool legacy servers (`text2icon server`, etc.) ficam como **deprecated**
  fallback; preferir `gamedev-model-server`. Arranque legacy exige
  `GAMEDEV_ALLOW_LEGACY_SERVER=1`.
- `ensure_vram_available`, `discover_server_pids`, `is_server_running` continuam;
  o caminho legacy de `ensure_vram` também é opt-in (mesma env).
- `generate` sync é a API principal das tools; async é opt-in.

## Testes

```bash
make test-modelserver
# suite de cobertura (peak/admit, scheduler, JobQueue, protocol — sem socket real):
pytest ModelServer/tests/test_modelserver_coverage_100.py -q
```

Guia monorepo: [`docs/TESTING_PT.md`](../docs/TESTING_PT.md) · [`docs/TESTING.md`](../docs/TESTING.md).

## Limites conhecidos

- **Sem kill CUDA mid-kernel** — cancel/progress só em boundaries (steps Diffusers /
  sampler / fases Hunyuan/Part/Terrain).
- **MultiGPU no UMS = pass-through** de `gpu_ids` às tools (`MultiGPUPlanner` fica
  dentro de cada generator; o supervisor não redistribui jobs entre GPUs).
- **Sem socket TCP / multi-máquina** — Unix domain socket local apenas.
- **Prioridades:** 2 níveis (`interactive` / `batch`).
- **WAL leve:** só jobs; não reconstitui modelos em VRAM após crash.
