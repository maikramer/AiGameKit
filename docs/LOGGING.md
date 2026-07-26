# File logging — GameDev tools + UMS

All Python CLIs and the Unified Model Server (UMS) write plain-text logs under
the GameDev cache. Console output stays Rich/ANSI; the file is a UTC mirror for
offline debug (failed GPU runs, queue stalls, worker crashes).

**Source:** [`Shared/src/gamedev_shared/logging.py`](../Shared/src/gamedev_shared/logging.py)  
**Português:** [LOGGING_PT.md](LOGGING_PT.md)

## Where

| Path | Meaning |
|------|---------|
| `~/.cache/gamedev/logs/<tool>-YYYY-MM-DD.log` | Default daily file (UTC date) |
| `$GAMEDEV_LOG_DIR/…` | Override directory |
| `$GAMEDEV_LOG_FILE` | Exact path (skips daily naming) |

Examples: `text2d-2026-07-16.log`, `ums-2026-07-16.log`, `gameassets-….log`.

Tool name comes from `setup_rich_click_module(tool=…)` in each package’s
`cli_rich.py`, or from `GAMEDEV_LOG_TOOL` / argv (`ums` /
`gamedev-model-server` → `ums`).

## Format

```
2026-07-16T20:01:02.123Z [INFO   ] === log start tool=ums pid=12345 ===
2026-07-16T20:01:03.456Z [INFO   ] [UMS] worker started backend=text3d
2026-07-16T20:01:10.789Z [WARN   ] gameassets.pipeline: stage paint retry
```

Levels in file: `DEBUG`, `DIM`, `INFO`, `STEP`, `SUCCESS`, `HEADER`, `WARN`, `ERROR`.
Default min level: `INFO` (`GAMEDEV_LOG_LEVEL`).

## What is mirrored

1. **`gamedev_shared.logging.Logger`** — every `info` / `warn` / `error` / `step` / …
2. **stdlib `logging.getLogger`** — bridged once per process to the same file
   (covers GameAssets, Text3D mesh paths, etc.)
3. **UMS** — `_log()` always writes to file; console only with `ums start -v`

`Logger.info(msg, console=False)` → file only (used by UMS when not verbose).

## Env vars

| Variable | Effect |
|----------|--------|
| `GAMEDEV_LOG_DIR` | Log directory (default `~/.cache/gamedev/logs`) |
| `GAMEDEV_LOG_FILE` | Exact file path |
| `GAMEDEV_LOG_TOOL` | Basename tool segment |
| `GAMEDEV_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARN` \| `ERROR` (default `INFO`) |
| `GAMEDEV_FILE_LOG` | `0` off; `1` force on (required under pytest) |
| `GAMEDEV_NO_FILE_LOG` | `1` disables file logging |

Under **pytest**, file logging is **off** unless `GAMEDEV_FILE_LOG=1` (avoids
polluting `~/.cache`).

## API (for tools / agents)

```python
from gamedev_shared.logging import Logger, configure_logging, current_log_path

configure_logging("mytool")  # opens/creates today's file + stdlib bridge
log = Logger(tool="mytool")
log.info("hello")
print(current_log_path())    # Path | None
```

CLI wiring: `setup_rich_click_module(..., tool="text2d")` calls
`configure_logging` at import time. UMS: `configure_logging("ums")` in
`ums start` (path shown in the start panel).

## Not covered

- **Materialize** (Rust) and **VibeGame** (TypeScript) use their own loggers.
- Profiler JSONL (`GAMEDEV_PROFILE_LOG`) is separate from this text log.

## Related

- Root env table: [`README.md`](../README.md) § Environment variables
- UMS: [`ModelServer/README.md`](../ModelServer/README.md)
- Shared module: [`Shared/README.md`](../Shared/README.md)
