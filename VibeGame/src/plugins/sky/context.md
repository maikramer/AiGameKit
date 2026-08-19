# Sky Plugin (context.md)

<!-- LLM:OVERVIEW -->

Two declarative sky modes sharing one job: put a sky in the background and a matching PMREM into `scene.environment` so PBR materials reflect it. `<EquirectSky url>` loads a panoramic image (PNG/JPG/HDR). `<Sky>` renders a procedural atmospheric dome (Preetham scattering, visible sun disc, animated shader clouds) whose sun also drives the first `directional-light` entity — one sun for sky, shadows, god rays and fog inscattering.

<!-- /LLM:OVERVIEW -->

## Layout

```
sky/
├── context.md     # This file
├── index.ts       # Exports
├── plugin.ts      # Plugin: SkyPlugin (recipes + systems + components + parsers)
├── components.ts  # EquirectSky (URL side-map + intensities) + ProceduralSky
├── recipes.ts     # equirectSkyRecipe + proceduralSkyRecipe
├── parser.ts      # equirectSkyParser + proceduralSkyParser
└── systems.ts     # EquirectSkyLoadSystem + ProceduralSkySystem
```

## Scope

- **In-scope**: `<EquirectSky>` (async texture load → background + PMREM IBL), `<Sky>` (atmospheric dome → background + PMREM IBL + sun-driven directional light), disposal of previous sky resources on swap/reload.
- **Out-of-scope**: pixel-level panorama rotation (imperative `extras/sky-env.ts`), sky texture generation (Skymap2D), time-of-day cycling.

## Entry Points

- **plugin.ts**: `SkyPlugin` — registered in `DefaultPlugins`.
- **systems.ts**: `EquirectSkyLoadSystem` (`simulation`), `ProceduralSkySystem` (`draw`, `before: LightSyncSystem`).
- **index.ts**: Re-exports `SkyPlugin`, `EquirectSky`, `ProceduralSky`, URL side-map helpers, both systems.

## Dependencies

- **Internal**: Core ECS (`State`, `System`, `defineQuery`), rendering (`getRenderingContext`, `DirectionalLight` component, `LightSyncSystem`), `core/utils/logger`.
- **External**: Three.js (`TextureLoader`, `PMREMGenerator`, `Sky` from `three/examples/jsm/objects/Sky.js`).

<!-- LLM:REFERENCE -->

### Components

#### EquirectSky

- `rotationDeg`: f32 — reserved (the declarative path applies the texture unrotated).
- `setBackground`: ui8 — `1` (default) sets `scene.background` to the raw equirect.
- `applied`: ui8 — load latch (`0` pending, `1` done).
- `environmentIntensity` / `backgroundIntensity`: f32 — `0` = loader defaults (0.45 / 1.2).
- **URL side-map**: `setEquirectSkyUrl(eid, url)` / `getEquirectSkyUrl(eid)` — strings don't fit TypedArrays.

#### ProceduralSky

- `turbidity`, `rayleigh`, `mieCoefficient`, `mieDirectionalG`: f32 — atmospheric scattering knobs (Sky.js uniforms).
- `sunElevation` / `sunAzimuth`: f32 — degrees; define the sun vector (toward the sun).
- `cloudCoverage` / `cloudDensity` / `cloudElevation`: f32 — shader cloud layer.
- `environmentIntensity`: f32 — `scene.environmentIntensity` (0 = fallback 0.65).
- `sunIntensity`: f32 — directional light intensity override (0 = keep the light entity's value).
- `driveLight`: ui8 — `1` rewrites the first directional-light entity's direction + warm horizon color each frame.

### Systems

#### EquirectSkyLoadSystem (`simulation`)

- Gated on renderer + scene existing; loads the texture once (`applied` latch + `inFlight` set).
- Background: the sharp equirect (`SRGBColorSpace`); env: `PMREMGenerator.fromEquirectangular`.
- `dispose`: frees the background texture and the PMREM render target.

#### ProceduralSkySystem (`draw`, `before: LightSyncSystem`)

- Maintains two `Sky` instances: the visible dome (sun disc on) and an IBL copy with `showSunDisc = 0` — the disc is ~10⁷ nits and would blow every material's diffuse irradiance through the PMREM; the sun's direct light is the directional light's job.
- On parameter change (signature compare): writes uniforms to both materials, regenerates the PMREM from the IBL copy (one cube render, not per frame), sets `scene.background = null` (the dome is the background).
- Every frame: advances cloud `time`, and when `driveLight` rewrites the first directional entity's `directionX/Y/Z` (toward-sun convention), warm→white color ramp by elevation, optional intensity override — before `LightSyncSystem` consumes the fields.
- `dispose`: removes the dome, disposes both materials/geometries and the PMREM target.

### Recipes

- **EquirectSky** — components `transform` + `equirect-sky`; attributes `url`, `rotation-deg`, `set-background`, `environment-intensity`, `background-intensity`.
- **Sky** — components `transform` + `procedural-sky`; attributes `turbidity`, `rayleigh`, `mie-coefficient`, `mie-directional-g`, `sun-elevation`, `sun-azimuth`, `cloud-coverage`, `cloud-density`, `cloud-elevation`, `environment-intensity`, `sun-intensity`, `drive-light`.

<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->

## Examples

```xml
<!-- Image sky -->
<EquirectSky url="/assets/sky/sky.png" environment-intensity="0.85"></EquirectSky>

<!-- Procedural sky; the sun drives the scene's directional light -->
<Sky
  sun-elevation="28" sun-azimuth="215" turbidity="2.6" rayleigh="1.7"
  cloud-coverage="0.32" environment-intensity="0.55" sun-intensity="3.0"
></Sky>
```

Pair `<Sky>` with a directional light for shadows — the light only needs the shadow settings, the sun supplies direction/color/intensity:

```xml
<GameObject directional-light="cast-shadow: 1; shadow-map-size: 4096; distance: 160; pcss: 1"></GameObject>
```

| Attribute (Sky)      | Type   | Default | Notes                                            |
| -------------------- | ------ | ------- | ------------------------------------------------ |
| `sun-elevation`      | number | `35`    | Degrees above the horizon; drives light warmth.  |
| `sun-azimuth`        | number | `160`   | Degrees around the horizon.                      |
| `turbidity`          | number | `2.8`   | Haze (2 clear → 10 murky).                       |
| `rayleigh`           | number | `1.6`   | Higher deepens the blue.                         |
| `cloud-coverage`     | number | `0.3`   | 0 none → 1 overcast.                             |
| `cloud-density`      | number | `0.35`  | Cloud opacity.                                   |
| `environment-intensity` | number | `0.65` fallback | IBL strength (`scene.environmentIntensity`). |
| `sun-intensity`      | number | keep   | `>0` overrides the directional light intensity.  |
| `drive-light`        | bool   | `true`  | Sun drives the first directional-light entity.   |

<!-- /LLM:EXAMPLES -->

## Known Limitations

### HDR sky + bloom

The procedural sky is HDR (luminance ≫ 1 near the sun). Bloom thresholds calibrated for LDR equirects (≈0.85) bloom the whole sky and wash out the frame — use `bloom-threshold` ≥ ~3 with a procedural sky (only the sun disc/glare blooms).

### PMREM ignores `texture.offset` / `texture.repeat`

`PMREMGenerator.fromEquirectangular()` samples through an internal shader that ignores offset/repeat/center. To rotate an equirect before PMREM, shift the bitmap pixels (`rotateEquirectBitmap()` in `extras/sky-env.ts`); the declarative path applies the background unrotated.

### Equirect must be 2:1 landscape

Center of image = horizon, top = zenith. Portrait/axis-swapped textures produce pillar artifacts; Skymap2D can emit wrong ratios (see its generator for auto-correction).
