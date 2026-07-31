# CLI Module

<!-- LLM:OVERVIEW -->

Headless state creation, XML parsing, text measurement, and query utilities for Node.js/Bun. Enables AI testing and video creation without browser/WebGL. E2E browser tests are run via the `vibegame` CLI and Playwright (see below).
<!-- /LLM:OVERVIEW -->

## Layout

```
cli/
├── context.md
├── index.ts       # Public exports
├── headless.ts    # Headless state creation
├── analyze/       # Offline world compile (vibegame analyze)
├── queries.ts     # Entity/sequence query utilities
└── text.ts        # Typr.js text measurement
```

## `vibegame analyze` (world compile)

Offline checks on `index.html` / world XML after `<Include>` expand + CityGrid expand:

```bash
vibegame analyze examples/simple-rpg/index.html
vibegame analyze --public-dir examples/simple-rpg/public --json
vibegame analyze --fail-on warn
vibegame analyze --scripts-dir examples/simple-rpg/src/scripts --plugins all
```

Reports:

- broken Includes, comma cell coords, CityGrid kids outside grid
- unknown recipe tags (error) + unknown attrs / soft Zod (warn) via Default+Rpg registry
- missing assets (`/…` **or** relative under `public/`; GLB + images/maps)
- missing entity `script=` / `<MonoBehaviour>` under `--scripts-dir` (auto `src/scripts`)
- empty spawners, duplicate `name=`, missing Player/camera, Terrain without heightmap
- RoadNetwork issues; solid overlaps (XZ **and** Y); solid∩Pad/Road as warn
- GLB bounds via meshopt/quantized path (`loadGlbCollisionMesh`) — LOD deliverables OK

Pads/Roads are ground — not solid↔solid errors. Exit `1` on errors (or warns with `--fail-on warn`).

**`overlap-max` (placeables):** optional metres on `<GameObject>` / `<Composition>` / `<Creature>`. Solid↔solid errors skip when penetration depth `min(Δx,Δz) ≤ max(allowA, allowB)`. Default omitted/`0` = strict (no depth tolerance). Wall joints often use `overlap-max="0.1"`.

API: `analyzeWorld({ entry, publicDir, scriptsDir?, plugins? })` from `vibegame/cli`.

## `vibegame` CLI (Playwright)

From the **VibeGame package root** with devDependencies installed (`bun install`, `bun run playwright:install`), the `vibegame` entrypoint (`scripts/vibegame-cli.mjs`) forwards to the Playwright CLI:

- `vibegame playwright <args…>` or `vibegame pw <args…>` — same as `playwright <args…>` with `cwd` at the repo root (uses `playwright.config.ts`).

Examples:

- `vibegame pw test`
- `vibegame pw test tests/playwright/simple-rpg-smoke.spec.ts`
- `vibegame pw test --ui` / `vibegame pw test --debug`
- `vibegame pw install chromium`

Environment variables (see `playwright.config.ts` comments):

- `PLAYWRIGHT_CDP_WS` — full WebSocket URL from the browser’s remote debugging endpoint.
- `PLAYWRIGHT_CDP_URL` — HTTP base (e.g. `http://127.0.0.1:9222`); `playwright.config.ts` fetches `{url}/json/version` via `http`/`https` do Node (sem `curl` no `PATH`).
- `PLAYWRIGHT_BASE_URL` — base URL when using CDP (default example: `http://127.0.0.1:3011`).

Without `playwright.config.ts` and without a local `node_modules/.bin/playwright`, the CLI exits with an error pointing to a full monorepo install.

## Scope

- **In-scope**: Headless state, XML parsing, DOM polyfills, text measurement, entity discovery, sequence inspection
- **Out-of-scope**: Rendering, WebGL, browser-only features

## Dependencies

- **Internal**: Core ECS (State), XML parser, TextPlugin, TweenPlugin
- **External**: jsdom, @fredli74/typr

<!-- LLM:REFERENCE -->

### Headless State

- `createHeadlessState(options)` - Creates State with `headless=true`
- `parseWorldXml(state, xml)` - Parses XML string, creates entities
- `loadWorldFromFile(state, path)` - Loads XML from file

### Text Measurement

- `loadFont(path)` - Loads TTF/OTF font
- `setHeadlessFont(state, font)` - Injects font for Word.width
- `measureTextWidth(font, text, fontSize)` - Pure text width calculation

### Entity Discovery

- `getEntityNames(state)` - All named entity names (sorted)
- `queryEntities(state, ...componentNames)` - Entity IDs by component
- `hasComponentByName(state, eid, name)` - Check if entity has component by name
- `getComponentData(state, eid, componentName)` - Get component field values
- `getEntityData(state, eid)` - Get all component data for entity

### Sequence Inspection

- `getSequenceInfo(state, name)` - Sequence state by name
- `getAllSequences(state)` - All sequences with state/progress

### Output

- `toJSON(snapshot)` - Structured JSON for AI parsing

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

```typescript
import {
  createHeadlessState, loadFont, parseWorldXml, setHeadlessFont,
  getEntityNames, getAllSequences, toJSON
} from 'vibegame/cli';
import { playSequence, resetSequence } from 'vibegame/tweening';

// Setup
const font = await loadFont('./font.ttf');
const state = createHeadlessState({ plugins: [TransformsPlugin, TweenPlugin, TextPlugin] });
setHeadlessFont(state, font);
parseWorldXml(state, xmlContent);

// Discover entities and sequences
const names = getEntityNames(state);
const sequences = getAllSequences(state);

// Step and snapshot with JSON output
state.step(0);
console.log(toJSON(state.snapshot({ entities: names, includeSequences: true })));

// Play sequence, step frames
const seq = state.getEntityByName('intro');
resetSequence(state, seq);
playSequence(state, seq);
for (let i = 0; i < 60; i++) state.step(1/60);

state.dispose();
```

<!-- /LLM:EXAMPLES -->
