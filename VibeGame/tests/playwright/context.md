# Playwright Tests

<!-- LLM:OVERVIEW -->
E2E test infrastructure for VibeGame using Playwright with Chromium. Provides a GameInspector class for state introspection via the `window.__VIBEGAME__` debug bridge, WebGL error capture, visual regression helpers, and custom fixtures that auto-wait for bridge initialization. Tests run against the `simple-rpg` example served by Vite.
<!-- /LLM:OVERVIEW -->

## Layout

```
tests/playwright/
├── context.md                # This file
├── debug-cycle.spec.ts       # 8 tests: state introspection via debug bridge
├── visual-regression.spec.ts # 4 tests: water/terrain verification
├── simple-rpg-smoke.spec.ts  # 2 tests: basic page load + terrain shader checks
├── helpers/
│   ├── game-inspector.ts     # GameInspector class, console/WebGL capture utilities
│   ├── visual-helpers.ts     # Canvas screenshot, pixel probe, dimension helpers
│   └── interaction.ts        # Keyboard, mouse, drag helpers
└── fixtures/
    └── vibegame-fixtures.ts  # vibegamePage + gameInspector Playwright fixtures
```

## Scope

- **In-scope**: State introspection via debug bridge, WebGL error detection, visual regression, E2E smoke tests, AI-driven debug cycle automation
- **Out-of-scope**: Unit tests, integration tests (see `tests/unit/`, `tests/integration/`), performance benchmarks

## Entry Points

- **debug-cycle.spec.ts**: Core AI debug cycle — bridge availability, entity inspection, state snapshots, screenshot baselines
- **visual-regression.spec.ts**: Water/terrain specific verification — water entity components, snapshot content validation
- **simple-rpg-smoke.spec.ts**: Basic page load validation

## Dependencies

- **Internal**: `@playwright/test`, `window.__VIBEGAME__` debug bridge (debug plugin)
- **External**: Playwright Chromium browser
- **Examples**: `examples/simple-rpg/` served via Vite dev server (port 30991)

## Running

```bash
# From VibeGame/
npx playwright install chromium    # first time
npx playwright test                # all Playwright tests
bun run test:debug                 # debug-cycle + visual-regression with JSON reporter
npx playwright test --ui           # interactive UI mode
npx playwright test --debug        # step-through debugger
```

## Configuration

- `playwright.config.ts` — testDir: `tests/playwright`, `fullyParallel: false`, auto-starts Vite on port 30991
- CDP mode: set `PLAYWRIGHT_CDP_WS` or `PLAYWRIGHT_CDP_URL` env vars to connect to running browser

<!-- LLM:REFERENCE -->
### GameInspector

```typescript
class GameInspector {
  isReady(): Promise<boolean>
  waitForBridge(timeout?: number): Promise<void>
  snapshot(): Promise<string>
  entities(): Promise<EntityData[]>
  entity(name: string): Promise<EntityData | null>
  component(eid: number, name: string): Promise<Record<string, number> | null>
  query(...componentNames: string[]): Promise<number[]>
  componentNames(): Promise<string[]>
  namedEntities(): Promise<Array<{ name: string; eid: number }>>
  step(dt?: number): Promise<void>
  captureConsoleErrors(): Promise<string[]>
  captureWebGLErrors(): Promise<string[]>
}
```

### Helper Functions

#### installConsoleCapture(page): void
Forwards browser console errors to `window.__VIBEGAME_CONSOLE_ERRORS` for retrieval

#### injectWebGLErrorCapture(page): Promise<void>
Monkey-patches `HTMLCanvasElement.prototype.getContext` to intercept shader compile and program link errors, stored in `window.__VIBEGAME_WEBGL_ERRORS`

#### screenshotCanvas(page, selector?): Promise<Buffer>
Full-page or element-specific screenshot

#### probeCanvasPixel(page, x, y, selector?): Promise<{ r, g, b, a }>
Read a single pixel from the WebGL/2D canvas

#### pressKey(page, key, duration?): Promise<void>
Key down/up with configurable hold duration

### Fixtures

#### vibegamePage: Page
Extended Playwright page that:
1. Installs console capture
2. Navigates to `/`
3. Waits for `#game-canvas` visible (30s timeout)
4. Injects WebGL error capture
5. Waits for `__VIBEGAME__` bridge (15s timeout)

#### gameInspector: GameInspector
Auto-created from vibegamePage, ready for immediate use
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### Basic State Introspection

```typescript
import { test, expect } from './fixtures/vibegame-fixtures';

test('check water entities', async ({ gameInspector }) => {
  const waterEntities = await gameInspector.query('water');
  expect(waterEntities.length).toBeGreaterThan(0);

  for (const eid of waterEntities) {
    const water = await gameInspector.component(eid, 'water');
    expect(water).not.toBeNull();
    expect(water!.size).toBeGreaterThan(0);
  }
});
```

### WebGL Error Detection

```typescript
test('no shader errors', async ({ gameInspector }) => {
  const glErrors = await gameInspector.captureWebGLErrors();
  expect(glErrors).toEqual([]);
});
```

### CDP Mode (Running Browser)

```bash
# Start Chrome with remote debugging
chromium --remote-debugging-port=9222

# Run tests against it
PLAYWRIGHT_CDP_URL=http://127.0.0.1:9222 npx playwright test
```
<!-- /LLM:EXAMPLES -->
