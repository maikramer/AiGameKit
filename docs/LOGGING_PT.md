# Logging em ficheiro — tools AiGameKit + vramd

Todas as CLIs Python e o Unified Model Server (vramd) gravam logs plain-text no
cache AiGameKit. Consola continua Rich/ANSI; ficheiro é espelho UTC para debug
offline (GPU falha, fila presa, crash de worker).

**Código:** [`Shared/src/aigamekit_shared/logging.py`](../Shared/src/aigamekit_shared/logging.py)  
**English:** [LOGGING.md](LOGGING.md)

## Onde

| Path | Significado |
|------|-------------|
| `~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log` | Ficheiro diário (data UTC) |
| `$AIGAMEKIT_LOG_DIR/…` | Override do diretório |
| `$AIGAMEKIT_LOG_FILE` | Path exacto (ignora naming diário) |

Exemplos: `text2d-2026-07-16.log`, `vramd-2026-07-16.log`, `gameassets-….log`.

Nome da tool: `setup_rich_click_module(tool=…)` em cada `cli_rich.py`, ou
`AIGAMEKIT_LOG_TOOL` / argv (`vramd` / `vramd` → `vramd`).

## Formato

```
2026-07-16T20:01:02.123Z [INFO   ] === log start tool=vramd pid=12345 ===
2026-07-16T20:01:03.456Z [INFO   ] [vramd] worker started backend=text3d
2026-07-16T20:01:10.789Z [WARN   ] gameassets.pipeline: stage paint retry
```

Níveis: `DEBUG`, `DIM`, `INFO`, `STEP`, `SUCCESS`, `HEADER`, `WARN`, `ERROR`.
Mínimo por omissão: `INFO` (`AIGAMEKIT_LOG_LEVEL`).

## O que vai para o ficheiro

1. **`Logger`** Shared — `info` / `warn` / `error` / `step` / …
2. **stdlib `logging.getLogger`** — bridge uma vez por processo (GameAssets, mesh, …)
3. **vramd** — `_log()` sempre em ficheiro; consola só com `vramd start -v`

`Logger.info(msg, console=False)` → só ficheiro (vramd sem verbose).

## Env

| Variável | Efeito |
|----------|--------|
| `AIGAMEKIT_LOG_DIR` | Dir (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | Path exacto |
| `AIGAMEKIT_LOG_TOOL` | Segmento do nome |
| `AIGAMEKIT_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` |
| `AIGAMEKIT_FILE_LOG` | `0` off; `1` força on (preciso sob pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | `1` desliga |

Sob **pytest** o ficheiro fica **off** salvo `AIGAMEKIT_FILE_LOG=1`.

## API

```python
from aigamekit_shared.logging import Logger, configure_logging, current_log_path

configure_logging("mytool")
log = Logger(tool="mytool")
log.info("olá")
print(current_log_path())
```

CLIs: `setup_rich_click_module(..., tool="text2d")` chama `configure_logging`.
vramd: `configure_logging("vramd")` no `vramd start` (path no painel de arranque).

## Fora de scope

- **Materialize** (Rust) e **VibeGame** (TS) — loggers próprios.
- Profiler JSONL (`AIGAMEKIT_PROFILE_LOG`) é outro canal.

## Ver também

- Tabela env raiz: [`README_PT.md`](../README_PT.md) § Variáveis de Ambiente
- vramd: [`Vramd/README.md`](../Vramd/README.md)
- Shared: [`Shared/README_PT.md`](../Shared/README_PT.md)
