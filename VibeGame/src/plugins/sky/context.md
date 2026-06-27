# Sky Plugin (context.md)

<!-- LLM:OVERVIEW -->

Declarative `<EquirectSky>` XML tag for loading an equirectangular sky texture (PNG/JPG/HDR) and applying it as `scene.background` (visual sky dome). This plugin owns the **background-only** path: it loads the texture once the renderer is live, sets `scene.background` + `scene.backgroundIntensity`, and dials `scene.environmentIntensity` down so the existing PMREM `RoomEnvironment` (from the rendering plugin) stays subtle for IBL/PBR. For full PMREM IBL from the sky texture itself (background **and** `scene.environment`), use the imperative `applyEquirectSkyEnvironment()` in [`extras/sky-env.ts`](../../extras/sky-env.ts) — see "Known Limitations" below for why rotation requires pixel-level bitmap manipulation there.

<!-- /LLM:OVERVIEW -->

## Layout

```
sky/
├── context.md     # This file
├── index.ts       # Exports
├── plugin.ts      # Plugin: EquirectSkyPlugin (recipe + system + component + parser)
├── components.ts  # EquirectSky (rotationDeg, setBackground, applied) + URL side-map
├── recipes.ts     # equirectSkyRecipe
├── parser.ts      # equirectSkyParser (XML element → component + URL map)
└── systems.ts     # EquirectSkyLoadSystem (load texture → scene.background)
```

## Scope

- **In-scope**: Declarative equirect sky via `<EquirectSky>`, one-shot async load gated on renderer readiness, background texture swap + dispose of previous background.
- **Out-of-scope**: PMREM generation from the sky texture (imperative API in `extras/sky-env.ts`), sky texture generation (Skymap2D Python tool), HDR EXR decoding internals.

## Entry Points

- **plugin.ts**: `EquirectSkyPlugin` — registered in `DefaultPlugins`.
- **systems.ts**: `EquirectSkyLoadSystem` (`simulation` group) — loads once renderer exists.
- **index.ts**: Re-exports `EquirectSkyPlugin`, `EquirectSky`, `getEquirectSkyUrl`, `setEquirectSkyUrl`, `EquirectSkyLoadSystem`.

## Dependencies

- **Internal**: Core ECS (`State`, `System`, `defineQuery`), rendering (`getRenderingContext`), `core/utils/logger`.
- **External**: Three.js (`TextureLoader`, `EquirectangularReflectionMapping`, `SRGBColorSpace`).
- **Related (not imported)**: [`extras/sky-env.ts`](../../extras/sky-env.ts) — imperative PMREM path (`applyEquirectSkyEnvironment`, `autoLoadSkyEnvironment`, `disposeSkyEnv`).

<!-- LLM:REFERENCE -->

### Component

#### EquirectSky

- `rotationDeg`: f32 — horizontal panorama rotation in degrees (reserved for future pixel-level rotation; the declarative path currently applies background directly without rotation).
- `setBackground`: ui8 — `1` (default) sets `scene.background`; `0` skips the background and only the intensity side-effects apply.
- `applied`: ui8 — latch; `0` = pending, `1` = load completed/failed. Prevents re-triggering each frame.
- **URL side-map**: `setEquirectSkyUrl(eid, url)` / `getEquirectSkyUrl(eid)` — strings don't fit in TypedArrays, so the URL is held in a module-level `Map<number, string>`.

### System

#### EquirectSkyLoadSystem

- Group: `simulation`
- Skips in headless mode and until `renderer` + `scene` exist (texture upload needs a live renderer).
- For each entity with `EquirectSky` where `applied === 0` and not in-flight:
  - Reads URL via `getEquirectSkyUrl(eid)`. If empty, marks `applied = 1` and skips.
  - Loads via `TextureLoader.loadAsync(url)`, sets `EquirectangularReflectionMapping` + `SRGBColorSpace`.
  - If `setBackground !== 0`: disposes the previous `scene.background` texture (if it was a texture and not the new one), assigns the new texture, sets `scene.backgroundIntensity = 1.2`.
  - Sets `scene.environmentIntensity = 0.45` (keeps PMREM RoomEnvironment subtle — the scene is already lit by hemisphere + directional lights; a full-strength IBL washes everything out).
  - Marks `applied = 1` on success or error (logged).
- `inFlight` Set prevents double-loading the same entity while the async load is pending.
- **`dispose(state)`**: disposes `scene.background` texture if present, nulls `scene.background`, clears `inFlight`, resets `applied = 0` for all sky entities. Does **not** touch `scene.environment` or the PMREM RT — those belong to `extras/sky-env.ts` (`disposeSkyEnv()`).

### Recipe

- **EquirectSky** — components: `equirect-sky`; parser attributes `url`, `rotation-deg`, `set-background`. Defaults: `rotationDeg: 0`, `setBackground: 1`, `applied: 0`.

### Plugin Config

- `parsers.EquirectSky` → `equirectSkyParser`.
- `defaults['equirect-sky']` → `{ rotationDeg: 0, setBackground: 1, applied: 0 }`.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Example

```xml
<EquirectSky url="/assets/sky/equirect.png" set-background="true"></EquirectSky>
```

| Attribute        | Type   | Default | Notes                                                                  |
| ---------------- | ------ | ------- | ---------------------------------------------------------------------- |
| `url`            | string | —       | Equirect PNG/JPG/HDR under `public/assets/sky/`.                       |
| `rotation-deg`   | number | `0`     | Reserved; declarative path applies background unrotated.               |
| `set-background` | bool   | `true`  | `false` skips `scene.background` (intensity side-effects still apply). |

For PMREM IBL + rotation, use the imperative API:

```ts
import { applyEquirectSkyEnvironment, run } from 'vibegame';

const state = await run();
await applyEquirectSkyEnvironment(state, '/assets/sky/equirect.png', {
  background: true,
  rotationDeg: 90,
  environmentIntensity: 0.15,
});
```

<!-- /LLM:EXAMPLES -->

## Known Limitations

### PMREM ignores `texture.offset` / `texture.repeat`

Three.js `PMREMGenerator.fromEquirectangular()` samples the source texture through an **internal shader** that does **not** honor `texture.offset`, `texture.repeat`, or `texture.center`. Setting those on the texture has no effect on the generated PMREM cubemap — the panorama appears at its original azimuth.

**Consequence:** to rotate an equirect horizontally before PMREM, the bitmap itself must be shifted at the **pixel level** (canvas drawImage split-and-swap in U), then fed to `PMREMGenerator`. This is what `applyEquirectSkyEnvironment({ rotationDeg })` in [`extras/sky-env.ts`](../../extras/sky-env.ts) does via `rotateEquirectBitmap()`. The declarative `<EquirectSky>` path applies the texture directly to `scene.background` without rotation (background honors `offset`/`repeat` normally, but the plugin does not currently wire `rotation-deg` into a background rotation).

### Equirect must be 2:1 landscape

Three.js equirect convention: `u = atan(dir.z, dir.x)`, `v = asin(dir.y)`. Center of image = horizon, top = zenith, bottom = nadir. Textures should be **2:1 aspect ratio, landscape** (e.g. 2048×1024). `applyEquirectSkyEnvironment()` warns if the ratio deviates by more than ~0.15.

- **Portrait** equirects (height > width) or **axis-swapped** textures map azimuth to the bitmap's vertical axis and produce "pillar" artifacts (vertical seams/bands) in the sky.
- The Skymap2D Flux-LoRA-Equirectangular model can emit wrong resolutions (e.g. 1024×768 instead of 2048×1024) with poles centered vertically; `Skymap2D/generator.py` auto-resizes and applies a 50% vertical shift to correct this at generation time. Validate the final PNG aspect ratio before handoff.

### Two code paths, two dispose owners

- **Declarative** (`<EquirectSky>` → `EquirectSkyLoadSystem`): owns `scene.background`. `dispose(state)` frees it.
- **Imperative** (`applyEquirectSkyEnvironment` in `extras/sky-env.ts`): owns the PMREM `WebGLRenderTarget` (`currentSkyRT`) and `scene.environment`. Call `disposeSkyEnv()` to free them — the plugin's `dispose()` does **not** cover this path.
