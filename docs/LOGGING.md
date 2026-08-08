# File logging — AiGameKit tools + vramd

All Python CLIs and the Unified Model Server (vramd) write plain-text logs under
the AiGameKit cache. Console output stays Rich/ANSI; the file is a UTC mirror for
offline debug (failed GPU runs, queue stalls, worker crashes).

**Source:** [`Shared/src/aigamekit_shared/logging.py`](../Shared/src/aigamekit_shared/logging.py)  
**Português:** [LOGGING_PT.md](LOGGING_PT.md)

## Where

| Path | Meaning |
|------|---------|
| `~/.cache/aigamekit/logs/<tool>-YYYY-MM-DD.log` | Default daily file (UTC date) |
| `$AIGAMEKIT_LOG_DIR/…` | Override directory |
| `$AIGAMEKIT_LOG_FILE` | Exact path (skips daily naming) |

Examples: `text2d-2026-07-16.log`, `vramd-2026-07-16.log`, `gameassets-….log`.

Tool name comes from `setup_rich_click_module(tool=…)` in each package’s
`cli_rich.py`, or from `AIGAMEKIT_LOG_TOOL` / argv (`vramd` /
`vramd` → `vramd`).

## Format

```
2026-07-16T20:01:02.123Z [INFO   ] === log start tool=vramd pid=12345 ===
2026-07-16T20:01:03.456Z [INFO   ] [vramd] worker started backend=text3d
2026-07-16T20:01:10.789Z [WARN   ] gameassets.pipeline: stage paint retry
```

Levels in file: `DEBUG`, `DIM`, `INFO`, `STEP`, `SUCCESS`, `HEADER`, `WARN`, `ERROR`.
Default min level: `INFO` (`AIGAMEKIT_LOG_LEVEL`).

## What is mirrored

1. **`aigamekit_shared.logging.Logger`** — every `info` / `warn` / `error` / `step` / …
2. **stdlib `logging.getLogger`** — bridged once per process to the same file
   (covers GameAssets, Text3D mesh paths, etc.)
3. **vramd** — `_log()` always writes to file; console only with `vramd start -v`

`Logger.info(msg, console=False)` → file only (used by vramd when not verbose).

## Env vars

| Variable | Effect |
|----------|--------|
| `AIGAMEKIT_LOG_DIR` | Log directory (default `~/.cache/aigamekit/logs`) |
| `AIGAMEKIT_LOG_FILE` | Exact file path |
| `AIGAMEKIT_LOG_TOOL` | Basename tool segment |
| `AIGAMEKIT_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` (default `INFO`) |
| `AIGAMEKIT_FILE_LOG` | `0` off; `1` force on (required under pytest) |
| `AIGAMEKIT_NO_FILE_LOG` | `1` disables file logging |

Under **pytest**, file logging is **off** unless `AIGAMEKIT_FILE_LOG=1` (avoids
polluting `~/.cache`).

## API (for tools / agents)

```python
from aigamekit_shared.logging import Logger, configure_logging, current_log_path

configure_logging("mytool")  # opens/creates today's file + stdlib bridge
log = Logger(tool="mytool")
log.info("hello")
print(current_log_path())    # Path | None
```

CLI wiring: `setup_rich_click_module(..., tool="text2d")` calls
`configure_logging` at import time. vramd: `configure_logging("vramd")` in
`vramd start` (path shown in the start panel).

## Not covered

- **Materialize** (Rust) and **VibeGame** (TypeScript) use their own loggers.
- Profiler JSONL (`AIGAMEKIT_PROFILE_LOG`) is separate from this text log.

## Related

- Root env table: [`README.md`](../README.md) § Environment variables
- vramd: [`Vramd/README.md`](../Vramd/README.md)
- Shared module: [`Shared/README.md`](../Shared/README.md)
