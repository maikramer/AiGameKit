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
`GAMEDEV_UMS_PRIORITY`, `GAMEDEV_UMS_DEBUG=1`, `GAMEDEV_UMS_AUTO_START_LOG`,
`GAMEDEV_ALLOW_LEGACY_SERVER=1` (text2icon/texture2d `server` legado).

**Cancel cooperativo:** adapters 2D consultam `_abort` entre steps Diffusers.
Hunyuan (text3d/paint3d): só entre fases (antes do generate).
**ETA / métricas:** `status`/`queue`/`stats` incluem `eta_sec` + `queue_metrics`
(cuts, wait p50/p95, `queue_full_count`).
**Inflight:** `MAX_INFLIGHT>1` só arranca jobs em paralelo se VRAM livre couber.

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
GAMEDEV_UMS_DEBUG=1 text2icon generate "x" -o out.png
```

## Backends suportados (9)

| Backend | Tool | VRAM (MiB) | Evict priority | API normalizada |
|---------|------|------------|----------------|-----------------|
| text2icon | Text2Icon | 3000 | 20 | `warmup()` |
| texture2d | Texture2D | 2500 | 20 | `warmup()` |
| text2d | Text2D | 4500 | 25 | `warmup()` |
| skymap2d | Skymap2D | 7000 | 25 | `warmup()` |
| text3d | Text3D | 8000 | 40 | `_load_hunyuan()` |
| paint3d | Paint3D | 4000 | 40 | context-manager |
| part3d | Part3D | 5200 | 35 | pipeline |
| text2sound | Text2Sound | 5000 | 30 | `load()` |
| terrain3d | Terrain3D | 6000 | 40 | procedural |

**VRAM** = estimativa do footprint (afinar com profiling real).
**Evict priority** = maior = manter carregado; menor = evicted primeiro.

## Instalação

```bash
cd Shared && pip install -e .
cd ../ModelServer && pip install -e .
# Ou: ./install.sh modelserver
```

## Uso

```bash
# Alias curto: ums ≡ gamedev-model-server
ums start
ums status          # backends + HOLDING/QUEUE + tip "não mates GPU"
ums status --json   # dump completo
ums queue           # jobs + timings/progress + tip
ums queue --json
ums wait <job_id>   # bloqueia até o job terminar
ums backends
ums preload text2icon
ums cancel <job_id>
ums evict text2icon
ums stats
ums doctor
ums stop
```

### Agents / anti-patterns

A fila UMS é a **autoridade** de quem usa a GPU. Se `nvidia-smi` mostra VRAM ocupada:

1. Corre `ums status` / `ums queue` — lê a linha `HOLDING: … | QUEUE: …`.
2. Espera (`ums wait <job_id>`, ou a tool com `--ums-stream`) **ou** `ums cancel <job_id>`.
3. **Não** mates PIDs GPU (`kill`, scripts, `--gpu-kill-others`) enquanto houver
   jobs UMS — o kill agressivo **recusa** nesse caso e as tools imprimem o tip
   `UMS_DO_NOT_KILL_TIP`.
4. Tools pesadas (`text3d`, `paint3d`) enfileiram no UMS **antes** de
   `ensure_vram` / kill; GPU prep só no fallback in-process.
5. Se a CLI diz `UMS indisponível — fallback in-process`, arranca
   `ums start` (ou deixa o auto-start) em vez de assumir que a GPU está “livre”.

### Integração com CLIs das tools

As CLIs GPU delegam no UMS (auto-start se `GAMEDEV_UMS_AUTO_START=1`):
`text2icon`, `texture2d`, `text2d`, `skymap2d`, `text3d`, `paint3d`, `part3d`,
`text2sound`, `terrain3d`.

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
text3d generate "goblin" -o goblin.glb --ums-priority interactive
# Batch GameAssets já exporta GAMEDEV_UMS_PRIORITY=batch nos subprocessos
```

`queue_full` **não** faz fallback in-process (evita segundo modelo na GPU) —
a CLI termina com erro; aumenta `GAMEDEV_UMS_MAX_QUEUE_DEPTH` ou espera.

### Coordenação de VRAM

**Pico = pesos(quant) + activação de inferência + `GAMEDEV_UMS_VRAM_SAFETY_MIB`
(default 384).** O UMS **recusa** load/generate se VRAM livre < pico (ex.: text3d
fp16 full ~8 GiB numa 6 GB) — tip: `sdnq-int4` / `--quality fast` /
`memory_efficient` (inferido como `sdnq-uint8` + activação reduzida; paint3d
deve enviar `sdnq_preset` ou `memory_efficient=true`). `status` mostra colunas
Peak / Act+.

Ferramentas pesadas, **no path in-process**, chamam
`ensure_vram_available(N, backend="text3d")`. Com UMS ativo → `ensure-vram` usa
`max(N, peak(backend))`. Sem UMS → release cego aos sockets legacy. Com jobs na
fila, `kill_gpu_compute_processes_aggressive` recusa matar (`respect_ums_queue=True`).

## Protocolo

JSON / NDJSON sobre Unix socket (`~/.cache/gamedev/model-server.sock`):

| Request | Comportamento |
|---------|---------------|
| `{"cmd":"generate","backend":"…",…}` | Enfileira + espera (sync). Opcional: `priority`, `stream` |
| `{"cmd":"submit","backend":"…",…}` | Enfileira; devolve `job_id` |
| `{"cmd":"poll","job_id":"…"}` | Estado do job |
| `{"cmd":"wait","job_id":"…"}` | Bloqueia até terminar |
| `{"cmd":"cancel","job_id":"…"}` | Cancela queued; running best-effort |
| `{"cmd":"queue"}` | Snapshot da fila |
| `{"cmd":"release"}` / `release`+`backend` | Evict |
| `{"cmd":"status"}` / `stats` | Estado + fila |
| `{"cmd":"preload","backend":"X"}` | Pré-aquece |
| `{"cmd":"ensure-vram","needed_mib":N}` | Evicta até N MiB livres |
| `{"cmd":"shutdown"}` | Graceful |

Com `stream: true` em `generate`/`wait`: linhas NDJSON
`queued` → `started` → `progress` → resultado final (`status` ok/error).

Cliente Shared: `delegate_to_ums`, `submit_to_ums`, `poll_ums_job`, `wait_ums_job`,
`cancel_ums_job`, `send_request_stream`.

## Retrocompatibilidade

- Per-tool legacy servers (`text2icon server`, etc.) ficam como **deprecated**
  fallback; preferir `gamedev-model-server`.
- `ensure_vram_available`, `discover_server_pids`, `is_server_running` continuam.
- `generate` sync é a API principal das tools; async é opt-in.
