# Testing — monorepo guide

Canonical guide for humans and agents. Short commands stay in [`AGENTS.md`](../AGENTS.md); this doc covers **coverage floor**, suite naming, and what “good” tests look like here.

Português: [`TESTING_PT.md`](TESTING_PT.md).

## Goals

1. **Every installer tool + accessory has ≥100 automated tests** (pytest cases, Bun `it`/`test`, or Rust `#[test]`).
2. **Coverage suites stay CPU-first** — no HF weight download, no CUDA required for the happy path of unit suites.
3. **Agent-first:** a cold agent can run `make test-<pkg>` (or package `.venv`) and trust green = contract still holds for pure helpers / CLI surface.

GPU / bpy / integration tests remain valuable; they must **skip** cleanly without hardware (`pytest.importorskip`, env guards). Do not make CI or laptop smoke depend on a live GPU.

## Tools & accessories (test targets)

| Package | Kind | How to run | Coverage entry points (examples) |
|---------|------|------------|----------------------------------|
| Shared | accessory lib | `make test-shared` | `tests/test_shared_coverage_100.py` |
| ModelServer (UMS) | accessory | `make test-modelserver` | `tests/test_modelserver_coverage_100.py` |
| AiGameKitLab | accessory | `make test-aigamekitlab` | `tests/test_aigamekitlab_coverage_suite.py`, `*_100b.py` |
| Text2D | tool | `make test-text2d` | `test_text2d_coverage_suite.py`, `*_100b.py` |
| Text2Icon | tool | `make test-text2icon` | `test_text2icon_coverage_suite.py`, `*_100b.py` |
| Text3D | tool | `make test-text3d` | `test_text3d_coverage_100.py` |
| Paint3D | tool | `make test-paint3d` | `test_paint3d_coverage_suite.py`, `*_100b.py` |
| Part3D | tool | `make test-part3d` | `test_part3d_coverage_100.py` |
| GameAssets | tool | `make test-gameassets` | `test_gameassets_coverage_100.py` |
| Texture2D | tool | `make test-texture2d` | `test_texture2d_coverage_100.py` |
| Skymap2D | tool | `make test-skymap2d` | `test_skymap2d_coverage_100.py` |
| Text2Sound | tool | `make test-text2sound` | `test_text2sound_coverage_100.py` |
| Terrain3D | tool | `make test-terrain3d` | `test_terrain3d_coverage_suite.py`, `*_100b.py` |
| Rocks3D | tool | `make test-rocks3d` | `test_rocks3d_coverage_100.py` |
| Rigging3D | tool | `make test-rigging3d` | `test_rigging3d_coverage_suite.py`, `*_100b.py` |
| Animator3D | tool | `make test-animator3d` | `test_animator3d_coverage_100.py` |
| Materialize | tool (Rust) | `make test-materialize` | `#[cfg(test)]` in `src/{preset,analyze,io,error,cli}.rs` |
| VibeGame | tool (Bun) | `make test-vibegame` | `tests/coverage-100.test.ts` + unit/integration |

Fallback: `<Pkg>/.venv/bin/pytest tests/ -q` (or `python3 -m pytest` with `PYTHONPATH=src:../Shared/src`).

Full CI Python+Rust: `make check` / `make test`. VibeGame is separate: `make test-vibegame`.

## Coverage suite convention

| Pattern | Use |
|---------|-----|
| `tests/test_<pkg>_coverage_100.py` | Single large suite (≥100 collected cases) |
| `tests/test_<pkg>_coverage_suite.py` + `*_100b.py` | Split when one file grew too large |
| `VibeGame/tests/coverage-100.test.ts` | Bun pure-helper floor |
| Materialize `#[cfg(test)] mod tests` inside `src/*.rs` | Binary-only crate — unit tests live next to code |

**Naming:** keep `coverage` in the filename so agents can find the floor suites quickly (`rg coverage tests/`).

**Counting:** pytest **collected** cases count (parametrize multiplies). Prefer real assertions on public/pure APIs over empty stubs.

### What to cover (priority order)

1. **UMS payloads** — `build_*_request` / peak+load opts (wrong keys = admit refuse / silent fallback).
2. **Hardware soft profiles** — `profile_from_specs` / `hw_auto_enabled` (pure; no GPU).
3. **Validators & defaults** — prompts, dimensions, presets, category maps.
4. **Pure mesh / audio / terrain math** — repair helpers, postprocess, DSP (local `import torch` inside the test if needed).
5. **CLI surface** — `CliRunner` / `python -m <tool> --help` (no GPU path).
6. **Materialize** — `Preset` roundtrip, `MapSelection`, analyze/classify on synthetic images, error exit codes.

### Anti-patterns (learned)

- **Fluff pads** that only assert “source tree non-empty” — delete; they inflate counts without protecting contracts.
- **Importing torch/diffusers at module top** in coverage suites — breaks collection; use local imports.
- **Requiring UMS socket or live GPU** in unit suites — mock / skip.
- **Post-hoc binary GLB hacks** as “tests of animation” — regenerate assets; test retarget math instead (see Animator3D / findings).
- **Editing vendored trees** (`Paint3D/.../hy3dpaint/`, SkinTokens) for testability — test wrappers/adapters only.
- **Importing native wheels in-process on CI** when the wheel can SIGILL (kills the whole pytest process) — probe in a subprocess first (see Text2Sound below).
- **Mutating shared module state** across Bun/pytest cases (`INPUT_CONFIG`, bitecs SoA defaults) without restore — flakes that pass locally and fail in CI order.

## GitHub Actions CI (`.github/workflows/ci.yml`)

Jobs on `main` push/PR:

| Job | What |
|-----|------|
| `lint` | `ruff check` + `ruff format --check` + `pre-commit run --all-files` (includes **mypy** on Shared) |
| `test-python` | matrix: Shared, GameAssets, Texture2D, Skymap2D, Rigging3D, Text2Sound, AiGameKitLab, Rocks3D, Animator3D — each `pip install -e Shared/.[dev]` then package `.[dev]` + pytest |
| `test-rust` | Materialize fmt/clippy/test (`continue-on-error`) |
| `vibegame` | Bun install + `check` + `lint` + `format:check` + `test` + `build` |

**Not in matrix** (heavy GPU stacks): Text2D, Text3D, Paint3D, Part3D, Terrain3D, ModelServer — run locally / with GPU. Root workflow **does** include VibeGame (do not rely only on `VibeGame/.github/`).

### Pitfalls learned (2026-07 CI green)

| Area | Failure mode | Contract / fix |
|------|--------------|----------------|
| Shared `[dev]` | `mesh_repair*` tests ImportError without numpy/scipy/trimesh | Keep those in `Shared/pyproject.toml` `[project.optional-dependencies] dev` — CI installs `Shared/.[dev]` |
| pre-commit mypy | Ruff green but lint job red | Fix Shared types; mypy runs after ruff in the same job |
| GameAssets Omni softfill | `ImportError: text3d` on runners without Text3D | `omni_ctrl._CATEGORY_OMNI_DEFAULTS_FALLBACK` + `_category_omni_defaults()` — softfill must not no-op when Text3D missing |
| Text2Sound pedalboard | Hosted CPU SIGILL kills pytest | `Text2Sound/tests/_heavy_deps.py` subprocess probe; skip mastering tests if unsafe |
| Texture2D dtype | `device="cuda"` silently falls back to CPU → float32 | Skip CUDA dtype asserts without real CUDA |
| Animator3D bpy mocks | Patching `sys.modules` alone does not replace already-imported `animator3d.bpy_ops` | `patch("animator3d.bpy_ops", …, create=True)` (+ module stub) |
| GameAssets `to_paint` | Asserting last `run_cmd` == simplify | Pipeline does simplify **then** re-`topology-fix` — assert simplify in `call_args_list` |
| VibeGame tsc | `Transform.dirty` on `Transform \| WorldTransform` | Guard `'dirty' in transform` before write |
| VibeGame tsconfig | Subpath imports (`vibegame/terrain`, …) fail `tsc` | Keep `paths` in `VibeGame/tsconfig.json` aligned with package exports |
| VibeGame flakes | Shared `INPUT_CONFIG` / NavMeshAgent SoA defaults / profiler customs | Restore config; refill SoA defaults in `beforeAll`; allow `ms >= 0` for custom timings |

Local parity: `make check` (Python+Rust) and `make check-vibegame` / `make test-vibegame`. Prefer `gh run list` / `gh run view --log-failed` over guessing.

## Package venvs

Always use the package-local interpreter so deps match install:

```bash
Text2D/.venv/bin/pytest Text2D/tests/test_text2d_coverage_suite.py -q
Materialize: cargo test --bin materialize-cli
VibeGame: bun test tests/coverage-100.test.ts
```

`./install.sh <tool>` creates `<Tool>/.venv` and its post-install hook adds the
`[dev]` extra (pytest, pytest-cov, ruff) there. If pytest is missing from that
venv, `make test-<tool>` prints a warning and falls back to the system
interpreter (CI has no per-package venv) — a fallback that surfaced as
`ModuleNotFoundError: huggingface_hub` on `make test-motion3d`. Fix it by
installing the tool, not by installing deps globally.

`AIGAMEKIT_FILE_LOG=1` may be needed under pytest for tools that gate file logging (see [`LOGGING.md`](LOGGING.md)).

## Extending coverage

When adding a tool or a critical pure helper:

1. Add tests in the package’s `tests/` (or `#[cfg(test)]` for Materialize).
2. Keep the package floor **≥100** collected cases.
3. Prefer extending an existing `*coverage*` file over inventing a third naming scheme.
4. Run the package target green before claiming done.
5. Update this doc’s table if the entry-point filename changes.

## Related docs

| Doc | Role |
|-----|------|
| [`AGENTS.md`](../AGENTS.md) | Mission + make targets + style |
| [`findings/`](findings/) | Runtime/GPU learnings (not unit-test floor) |
| [`MODEL_FINDINGS.md`](MODEL_FINDINGS.md) | VRAM / kernels / Omni hub |
| [`mission/04-agent-first-reproducibility.md`](mission/04-agent-first-reproducibility.md) | Why contract tests matter |
| Package `README.md` / `AGENTS.md` | Per-tool notes |
