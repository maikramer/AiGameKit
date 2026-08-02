# Vite Plugin Module

Vite plugins for VibeGame development and build tooling.

## Structure

- **index.ts** — Plugin exports (`vibegame`, console forwarding, watch helpers, asset HMR)
- **watch-ignored.ts** — Shared `server.watch.ignored` patterns (ENOSPC guard)
- **console-plugin.ts** — Console forwarding for development
- **hot-reload.ts** / **hot-reload-client.ts** — Optional asset HMR via `fs.watch` + client listener
- **public-live-serve.ts** — Serve `public/` live helpers (examples)
- **silence-typr.ts** — Neutralize Typr OpenType `console.debug` noise (GPOS/GSUB)

## Plugins

### vibegame()

- Aliases Rapier3D to the compatible WASM build
- Merges `VIBEGAME_SERVER_WATCH_IGNORED` into `server.watch.ignored` so examples
  never chokidar-watch graphify caches, GameAssets workdirs, or huge `public/assets`
  blobs (still served as static files; HMR stays on `src/` / HTML / TS)
- Merges `optimizeDeps.exclude` for WASM-heavy packages that break Vite prebundle /
  Firefox DevTools sourcemaps:
  - `recast-navigation`, `@recast-navigation/three`
  - `@pmndrs/uikit`, `yoga-layout` (yoga WASM → `URL constructor: is not a valid URL`
    / `sourceMappingURL: null` when inlined in `.vite/deps`)
  - `@dimforge/rapier3d-compat`, `@dimforge/rapier3d` (same DevTools sourcemap noise)
- Engine TS edits force **full page reload** (`vibegameForceFullReload`, debounced)
  — soft HMR orphans WebGL/KTX2/Rapier. Unload uses **lightweight**
  `releaseRuntimeGpuResources` on `pagehide` only (never heavy `destroy()` on
  `vite:beforeFullReload` — that can hang mid-boot and block `location.reload`)
- Includes `silenceTyprOpentypeNoise()` (see below)

Examples may still list the same excludes locally (e.g. simple-rpg) for clarity;
`vibegame()` merges without dropping app-specific entries (`vibegame`,
`@gltf-transform/*`, …).

After changing excludes: clear Vite cache (`rm -rf node_modules/.vite`) and restart.

### silenceTyprOpentypeNoise()

Transform-time noop for Typr (via `troika-three-text`) messages:

`console.debug("unsupported GPOS/GSUB table LookupType", …)`

Harmless skipped OpenType lookups; Firefox surfaces them as console noise when
FloatingText / other troika `Text` loads Roboto (or any font with those tables).
Tests: `tests/unit/vite/silence-typr.test.ts`.

### consoleForwarding()

Development plugin that forwards browser console output to terminal:

- Injects console override into main.ts via transform hook
- Captures console.log, warn, error, debug methods
- Extracts file/line context from stack traces
- Formats output with timestamps and ANSI color codes
- Uses Vite's HMR WebSocket for message transport
- Only active in serve mode with enforce: 'post'
- Transform matcher is substring-based (`/src/main.ts` / `/src/main.js`) — near-miss paths may also inject
- Sends are gated on the HMR socket state (`vite:ws:connect`/`disconnect`): while
  disconnected (e.g. right after a reload) messages queue instead of calling
  `import.meta.hot.send` — Vite 8's module-runner client throws
  `SendBeforeConnectError` there, and the client's error log would re-enter the
  console override and cascade. `SendBeforeConnectError` is never surfaced or
  forwarded; a bounded boot self-heal covers the connect event racing module eval.

### vibegameAssetHotReload()

- Watches asset dirs (default `public/assets`) with Node `fs.watch`
- Sends `vibegame:asset-update` over the Vite WS; `hotUpdate` (Vite 8) /
  `handleHotUpdate` (Vite 6/7) suppresses default HMR for matched extensions
- Client: `initAssetHotReload()` from `hot-reload-client.ts`

### vibegameForceFullReload()

- Forces one debounced full page reload for engine/example TS changes (soft HMR
  leaks WebGL contexts / WASM in Firefox)
- Implements **both** hook names: `hotUpdate` (Vite 8 — `handleHotUpdate` is
  skipped for the client environment there) and `handleHotUpdate` (Vite 6/7)
- Returning `[]` from the hook suppresses Vite's own per-file reload; the
  debounce (200 ms) coalesces multi-file edits into a single reload — without
  the new hook, Vite 8 reloads once per saved file and aborts the game mid-boot

## Usage

```typescript
import { defineConfig } from 'vite';
import { vibegame, consoleForwarding, vibegameAssetHotReload } from 'vibegame/vite';

export default defineConfig({
  plugins: [vibegame(), consoleForwarding(), vibegameAssetHotReload()],
});
```

## Tests

Unit coverage lives in `tests/unit/vite/` (`public-live-serve`, `silence-typr`,
`console-*`, watch/hot-reload where present). See [`docs/TESTING.md`](../../docs/TESTING.md).

## Implementation

Console forwarding works by:

1. Transforming the main entry file to inject console overrides
2. Using `import.meta.hot.send()` to transmit messages
3. Server-side WebSocket listener formats and outputs to terminal
4. Preserves original console behavior in browser
