---
description: Run the full repo CI gate (lint + format check + typecheck + tests across Python, Rust, VibeGame) via the root Makefile.
---
Run the repo's pre-push gate. The root `Makefile` aggregates every language.

Scope: $ARGUMENTS

- No scope given → run the full gate:

  `make check`

  (`check: lint fmt-check typecheck test` — ruff + cargo clippy, format checks,
  mypy on Shared, then pytest per Python project + cargo test + VibeGame.)

- A project/area named in $ARGUMENTS → run only the narrow target (faster):
  - Python project: `make test-<project>` (e.g. `make test-text3d`)
  - Rust: `make test-materialize`
  - VibeGame: `make check-vibegame && make test-vibegame`
  - Lint only: `make lint` · Format check only: `make fmt-check`

Report failing targets with the exact error lines. If all pass, say so plainly
with the green count. Do not auto-fix unless asked — for fixes run `make fmt`
then `make lint` and hand back.
