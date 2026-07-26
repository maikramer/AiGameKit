# Tests

<!-- LLM:OVERVIEW -->

Test tree for VibeGame: Bun unit/integration/e2e plus Playwright browser E2E. Unit tests live under `unit/<plugin>/` (and sometimes `unit/plugins/<plugin>/`). Full guide: [`docs/TESTING.md`](../docs/TESTING.md).
<!-- /LLM:OVERVIEW -->

## Layout

```
tests/
├── context.md           # This file
├── unit/                # bun:test — per-plugin + core + vite + extras
│   ├── <plugin>/        # Canonical mirror of src/plugins/<plugin>/
│   ├── plugins/<plugin>/# Legacy nested layout (still counted)
│   ├── vite/            # src/vite tooling plugins
│   ├── core/            # ECS / runtime helpers
│   └── extras/          # gltf-bridge, animator, etc.
├── integration/         # Multi-plugin + XML recipes
├── e2e/                 # Broader bun e2e
├── playwright/          # Chromium + simple-rpg (see playwright/context.md)
├── fixtures/            # Small Vite harnesses (HUD, float, minimap)
├── helpers/             # Shared stubs (e.g. webgl-stub)
└── manual-tests/        # Ad-hoc scripts (not CI)
```

## Scope

- **In-scope**: Plugin coverage (≥100 `it()` each), isolation of globals, Vite plugin tests, integration recipes, Playwright smoke/debug.
- **Out-of-scope**: Python GameAssets GPU pipelines (monorepo other packages).

## Isolation

Never leave `performance` / `document` / `window` polluted. Do not assign jsdom's `performance` to `globalThis.performance`. Details: [`docs/TESTING.md`](../docs/TESTING.md#isolation-contract-obrigatório).

## Running

```bash
bun test tests/unit
bun test tests/unit/<plugin> tests/unit/plugins/<plugin>
bun test tests/unit/vite
bun test tests/integration tests/e2e
bun run test:playwright
```
