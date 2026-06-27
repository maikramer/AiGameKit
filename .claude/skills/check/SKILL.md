---
name: check
description: Run the full repo CI gate (lint + format check + typecheck + tests across Python, Rust, and VibeGame) via the root Makefile. Use before pushing or when the user says "run check", "/check", "verify everything", or wants the pre-push gate.
disable-model-invocation: true
---

# check — full CI gate

Run the repo's complete pre-push gate. The root `Makefile` aggregates every
language.

## Default (everything)

```bash
make check
```

`check: lint fmt-check typecheck test` — ruff + cargo clippy, format checks,
mypy on Shared, then pytest per Python project + cargo test + VibeGame.

## Scoped runs

If the user names a project or only one area changed, run the narrow target
instead of the full gate (faster):

- Python project: `make test-<project>` (e.g. `make test-text3d`)
- Rust: `make test-materialize`
- VibeGame: `make check-vibegame && make test-vibegame`
- Lint only: `make lint`  · Format check only: `make fmt-check`

## Reporting

Report the failing targets and the exact error lines. If everything passes,
say so plainly with the green count. Do not auto-fix unless asked — for fixes
run `make fmt` (format) and `make lint` then hand back to the user.
