# Test Helpers

Shared utilities for VibeGame tests (under `tests/helpers/`).

## `webgl-stub.ts`

A minimal, opt-in WebGL stub that lets `THREE.WebGLRenderer` be constructed and
render under Bun/JSDOM without a real GPU. It exists so rendering/camera/
material systems gain **smoke-level** CI coverage (does the system crash? are
the types right?) — it provides **no visual correctness**.

### Usage

```ts
import {
  installWebGLStub,
  uninstallWebGLStub,
} from '../helpers/webgl-stub';

describe('My rendering test', () => {
  beforeEach(() => {
    // JSDOM globals must be set up first (document, window, HTMLCanvasElement).
    installWebGLStub();
  });

  afterEach(() => {
    uninstallWebGLStub();
  });

  it('exercises the renderer', async () => {
    // state.headless MUST be false to enter the render path.
    const state = new State();
    state.registerPlugin(RenderingPlugin);
    state.headless = false;
    // ...canvas + RenderContext entity, then state.step()...
  });
});
```

### How it works

- `installWebGLStub()` monkey-patches `HTMLCanvasElement.prototype.getContext`
  so `getContext('webgl2' | 'webgl' | 'experimental-webgl')` returns a `Proxy`
  that no-ops every method call and returns sensible defaults (e.g.
  `getShaderParameter`/`getProgramParameter` return `true`, `getError` returns
  `0`, `createShader`/`createProgram`/`createBuffer`/`createTexture` return
  `{}`, `getUniformLocation` returns `{}`, `checkFramebufferStatus` returns
  `FRAMEBUFFER_COMPLETE`, `getParameter` returns typed values per GLenum).
- It also exposes `WebGLRenderingContext` / `WebGL2RenderingContext` on the
  global scope if they are missing.
- `uninstallWebGLStub()` restores the original `getContext` and removes any
  globals it added.

The stub is **never** installed globally — call it explicitly per test and
always pair `install` with `uninstall` to avoid leaking the patch.

### What it covers

The stub is sufficient for `THREE.WebGLRenderer` construction, environment map
generation (`PMREMGenerator`/`RoomEnvironment`), and a full `renderer.render()`
pass. See `tests/integration/rendering/webgl-stub-smoke.test.ts` for a
reference smoke test.
