# Manual / automated browser checks

Lightweight Playwright scripts that drive the **simple-rpg** example in a real
Chromium and assert runtime behaviour (boot, physics body, movement, grounded
state). They complement the formal specs in `tests/playwright/` — use these for
fast, scriptable diagnosis while iterating on the engine.

## Run

1. Start the example dev server (leave it running):

   ```bash
   cd examples/simple-rpg
   BROWSER=none npx vite dev --host 127.0.0.1 --port 30991 --strictPort
   ```

2. From the repo root, run a single check or all of them:

   ```bash
   node tests/manual-tests/check-movement.mjs
   node tests/manual-tests/run-all.mjs
   ```

Each `check-*.mjs` exits non-zero on failure, so `run-all.mjs` (and CI) can gate on it.

## Env knobs

| Var             | Default                   | Purpose                         |
| --------------- | ------------------------- | ------------------------------- |
| `VIBE_URL`      | `http://127.0.0.1:30991/` | Target URL                      |
| `VIBE_BOOT_MS`  | `8000`                    | Wait after load before sampling |
| `VIBE_HEADLESS` | `1`                       | Set `0` to watch the browser    |

## Checks

- **check-boot** — canvas present, hero has a Rapier body, no real console errors
  (the `WebGPU is not available, running under WebGL2 backend` line is benign).
- **check-movement** — holding `W` translates the hero and keeps it near the ground.
- **check-grounded** — regression guard: while walking, the kinematic controller
  must stay `grounded === 1`. If it flickers to 0 the animation system flips to
  the fall/jump pose mid-walk.

## Writing new checks

Import helpers from `lib.mjs`:

```js
import { withGame, heroDebug, focusCanvas, holdKey, sampleWhileHolding, report } from './lib.mjs';

await withGame(async (page, { logs }) => {
  // page is a Playwright Page already booted into the game.
  // window.__heroDebug() and window.__heroState are available in evaluate().
  const ok = report('my assertion', true);
  process.exit(ok ? 0 : 1);
});
```

Name the file `check-<topic>.mjs` so `run-all.mjs` picks it up automatically.
