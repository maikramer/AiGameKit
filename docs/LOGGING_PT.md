# Logging em ficheiro — tools GameDev + UMS

Todas as CLIs Python e o Unified Model Server (UMS) gravam logs plain-text no
cache GameDev. Consola continua Rich/ANSI; ficheiro é espelho UTC para debug
offline (GPU falha, fila presa, crash de worker).

**Código:** [`Shared/src/gamedev_shared/logging.py`](../Shared/src/gamedev_shared/logging.py)  
**English:** [LOGGING.md](LOGGING.md)

## Onde

| Path | Significado |
|------|-------------|
| `~/.cache/gamedev/logs/<tool>-YYYY-MM-DD.log` | Ficheiro diário (data UTC) |
| `$GAMEDEV_LOG_DIR/…` | Override do diretório |
| `$GAMEDEV_LOG_FILE` | Path exacto (ignora naming diário) |

Exemplos: `text2d-2026-07-16.log`, `ums-2026-07-16.log`, `gameassets-….log`.

Nome da tool: `setup_rich_click_module(tool=…)` em cada `cli_rich.py`, ou
`GAMEDEV_LOG_TOOL` / argv (`ums` / `gamedev-model-server` → `ums`).

## Formato

```
2026-07-16T20:01:02.123Z [INFO   ] === log start tool=ums pid=12345 ===
2026-07-16T20:01:03.456Z [INFO   ] [UMS] worker started backend=text3d
2026-07-16T20:01:10.789Z [WARN   ] gameassets.pipeline: stage paint retry
```

Níveis: `DEBUG`, `DIM`, `INFO`, `STEP`, `SUCCESS`, `HEADER`, `WARN`, `ERROR`.
Mínimo por omissão: `INFO` (`GAMEDEV_LOG_LEVEL`).

## O que vai para o ficheiro

1. **`Logger`** Shared — `info` / `warn` / `error` / `step` / …
2. **stdlib `logging.getLogger`** — bridge uma vez por processo (GameAssets, mesh, …)
3. **UMS** — `_log()` sempre em ficheiro; consola só com `ums start -v`

`Logger.info(msg, console=False)` → só ficheiro (UMS sem verbose).

## Env

| Variável | Efeito |
|----------|--------|
| `GAMEDEV_LOG_DIR` | Dir (default `~/.cache/gamedev/logs`) |
| `GAMEDEV_LOG_FILE` | Path exacto |
| `GAMEDEV_LOG_TOOL` | Segmento do nome |
| `GAMEDEV_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` |
| `GAMEDEV_FILE_LOG` | `0` off; `1` força on (preciso sob pytest) |
| `GAMEDEV_NO_FILE_LOG` | `1` desliga |

Sob **pytest** o ficheiro fica **off** salvo `GAMEDEV_FILE_LOG=1`.

## API

```python
from gamedev_shared.logging import Logger, configure_logging, current_log_path

configure_logging("mytool")
log = Logger(tool="mytool")
log.info("olá")
print(current_log_path())
```

CLIs: `setup_rich_click_module(..., tool="text2d")` chama `configure_logging`.
UMS: `configure_logging("ums")` no `ums start` (path no painel de arranque).

## Fora de scope

- **Materialize** (Rust) e **VibeGame** (TS) — loggers próprios.
- Profiler JSONL (`GAMEDEV_PROFILE_LOG`) é outro canal.

## Ver também

- Tabela env raiz: [`README_PT.md`](../README_PT.md) § Variáveis de Ambiente
- UMS: [`ModelServer/README.md`](../ModelServer/README.md)
- Shared: [`Shared/README_PT.md`](../Shared/README_PT.md)
