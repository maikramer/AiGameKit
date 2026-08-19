# Effect Registry

Post-processing system based on the [`postprocessing`](https://www.npmjs.com/package/postprocessing) npm package with a registry pattern for extensible effects.

**Location:** `src/plugins/postprocessing/`

## Files

| File | Description |
|------|-------------|
| `components.ts` | ECS SOA component (`Postprocessing`) with all effect fields |
| `effect-registry.ts` | Registry API (`registerEffect`, `getEffectDefinitions`, `unregisterEffect`) |
| `builtin-effects.ts` | Built-in effects: Bloom, SMAA, FXAA, Vignette, ChromaticAberration, ToneMapping, SSAO, DepthOfField, HeightFog, GodRays, SSR |
| `height-fog-effect.ts` | `HeightFogEffect` — depth-based exponential height fog with sun inscattering and sky haze |
| `sun-source.ts` | Shared first-directional-light lookup (god rays + height-fog inscattering) |
| `reflection-pass.ts` | `ReflectionPass` — SSR adapter wrapping three's SSRPass |
| `composer.ts` | `EffectComposer` builder with HDR (`HalfFloatType`) and effect merging |
| `systems.ts` | `PostprocessingBuildSystem` — builds the pipeline once renderer + camera are ready |
| `plugin.ts` | `PostprocessingPlugin` — system, component, config (defaults + enums) |
| `index.ts` | Public re-exports |

## How It Works

The `PostprocessingBuildSystem` (in `systems.ts`) runs in the `draw` group, after `CameraSyncSystem`. On the first frame where a `Postprocessing` entity is enabled and the renderer + camera exist, it:

1. Registers built-in effects (once, lazy)
2. Iterates `getEffectDefinitions()`, calling `create()` for each
3. Separates regular effects from convolution effects (ChromaticAberration)
4. Builds an `EffectComposer` with `RenderPass` + `EffectPass`(es)
5. Stores in `context.postProcessing` — the runtime render loop uses this when present

All non-convolution effects are **merged into a single `EffectPass`** (one draw call). ChromaticAberration goes in a separate pass (package constraint: only one convolution effect per pass).

## XML / Declarative Usage

Post-processing is declared as a component attribute on any entity (typically a `GameObject`):

```xml
<GameObject postprocessing="enabled: 1; bloom: 1; vignette: 1; aa: smaa; tone-mapping: agx"></GameObject>
```

### Available Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `ui8` | 1 | Master on/off |
| `bloom` | `ui8` | 1 | Bloom on/off |
| `bloom-strength` | `f32` | 0.6 | Bloom intensity |
| `bloom-radius` | `f32` | 0.4 | Bloom blur radius |
| `bloom-threshold` | `f32` | 0.85 | Luminance threshold |
| `chromatic-aberration` | `ui8` | 1 | CA on/off |
| `ca-strength` | `f32` | 0.003 | CA offset magnitude |
| `vignette` | `ui8` | 1 | Vignette on/off |
| `vignette-offset` | `f32` | 0.35 | Vignette offset |
| `vignette-darkness` | `f32` | 0.5 | Vignette darkness |
| `aa` | enum | `smaa` | Anti-aliasing: `off`, `fxaa`, `smaa` |
| `tone-mapping` | enum | `agx` | Tone mapping: `off`, `agx`, `aces`, `neutral`, `reinhard` |
| `tone-mapping-exposure` | `f32` | 1.0 | Exposure multiplier |
| `ssao` | `ui8` | 0 | SSAO on/off |
| `ssao-intensity` | `f32` | 1.0 | SSAO intensity |
| `ssao-radius` | `f32` | 1.0 | SSAO radius |
| `depth-of-field` | `ui8` | 0 | DoF on/off |
| `dof-focus-distance` | `f32` | 0.01 | Focus distance |
| `dof-focus-range` | `f32` | 0.5 | Focus range |
| `dof-bokeh-scale` | `f32` | 3.0 | Bokeh blur scale |
| `height-fog` | `ui8` | 0 | Height fog on/off (composer: analytic post fog; without composer: `FogExp2` fallback) |
| `fog-color` | `u32` | `0x10131a` | Fog tint (hex) |
| `fog-density` | `f32` | 0.06 | Exponential density (1/m) |
| `fog-height` | `f32` | 2.0 | World Y of maximum density (m) |
| `fog-falloff` | `f32` | 0.15 | Vertical decay above `fog-height` (m) |
| `fog-noise` | `f32` | 0.5 | World-space FBM noise modulation (0..1) |
| `fog-sun-influence` | `f32` | 0.8 | Forward-scattering strength toward the sun |
| `fog-sky-haze` | `f32` | 0.5 | Horizon haze on sky pixels (aerial perspective) |
| `god-rays` | `ui8` | 0 | God rays on/off (sun source follows the first directional light) |
| `ssr` | `ui8` | 0 | Screen-space reflections on shiny meshes |
| `ssr-opacity` | `f32` | 0.5 | Reflection blend strength |
| `ssr-max-distance` | `f32` | 180 | Ray march length (m) |
| `ssr-thickness` | `f32` | 0.018 | Surface thickness tolerance (m) |
| `ssr-resolution-scale` | `f32` | 0.5 | March buffer scale (perf vs sharpness) |

## Registry API

### `EffectDefinition`

```ts
interface EffectDefinition {
  readonly key: string;
  readonly component?: Component;
  create(state, entity, renderer, scene, camera): Effect | null;
  update?(state, entity, effect): void;
  readonly position?: 'first' | 'last';
  readonly order?: number; // sort key within a position bucket (lower = earlier)
}
```

Regular-pass ordering by `order`: SSAO (`-10`) → height fog (`-5`) → bloom/vignette/DoF/etc. (`0`, registration order). This keeps occlusion under the atmosphere and lets fog bloom: the haze the camera sees is graded and bloomed like the scene behind it.

### Functions

```ts
registerEffect(definition: EffectDefinition): void
getEffectDefinitions(): readonly EffectDefinition[]
unregisterEffect(key: string): boolean
```

## Creating a Custom Effect

```ts
import { registerEffect } from 'vibegame/postprocessing';
import { MyCustomEffect } from './my-custom-effect-component';
import { SomePostprocessingEffect } from 'postprocessing';

registerEffect({
  key: 'my-effect',
  create(state, entity, renderer, scene, camera) {
    if (!MyCustomEffect.enabled[entity]) return null;
    return new SomePostprocessingEffect({
      intensity: MyCustomEffect.intensity[entity],
    });
  },
  update(state, entity, effect) {
    effect.intensity = MyCustomEffect.intensity[entity];
  },
});
```

The `PostprocessingBuildSystem` automatically discovers and applies registered effects.

## Runtime Toggle

Effects can be toggled at runtime by flipping ECS component fields and disposing the pipeline:

```ts
Postprocessing.bloom[entity] = Postprocessing.bloom[entity] ? 0 : 1;
const ctx = getRenderingContext(state);
ctx.postProcessing?.dispose();
ctx.postProcessing = undefined;
```

The system rebuilds the pipeline on the next frame.

## Performance

The `postprocessing` npm package **merges multiple effects into a single fullscreen shader** via `EffectPass`. This means N effects = 1 draw call (vs N draw calls with Three.js built-in `EffectComposer`). ChromaticAberration requires a separate pass due to the convolution constraint.
